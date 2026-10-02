"""Model catalog: verify Hugging Face models against the pinned vLLM-Omni
build, and add / remove models from the UI.

Verification runs `ttsbench-probe.py` inside the *backend image* itself, so
the answer reflects exactly the vLLM-Omni version that will serve the model.
The probe uses vLLM-Omni's own pipeline resolution and TTS adapter detection
and only downloads config files, never weights.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import docker
import httpx
import yaml
from docker.types import Mount

from .settings import ModelSpec, Settings, load_registry

log = logging.getLogger("ttsbench.catalog")

HF_API = "https://huggingface.co/api/models"
PROBE_MARK = "TTSBENCH_PROBE "
# Bump when the verification logic changes so stale cached results are redone.
VERIFY_VERSION = 2
REPO_RE = re.compile(r"^[A-Za-z0-9][\w.-]*/[\w.-]+$")

# How each vLLM-Omni TTS adapter takes a natural-language voice description.
# "native": documented voice design. "instructions": the adapter forwards
# `instructions`, but voice design is not documented. "text_prefix": the
# description is written into the input text. "none": no prompt path.
ADAPTER_PRESETS: dict[str, dict[str, Any]] = {
    "breeze_tts_2": {"voice_design": "native", "request": {"input": "{text}", "instructions": "{prompt}"}},
    "omnivoice": {"voice_design": "instructions", "request": {"input": "{text}", "instructions": "{prompt}"}},
    "ming_tts": {"voice_design": "instructions", "request": {"input": "{text}", "instructions": "{prompt}"}},
    "ming_flash_omni_tts": {"voice_design": "instructions",
                            "request": {"input": "{text}", "instructions": "{prompt}"}},
    "voxcpm2": {"voice_design": "text_prefix", "request": {"input": "({prompt}){text}"}},
    "higgs_audio_v2": {"voice_design": "none", "request": {"input": "{text}"},
                       "message": "This adapter rejects `instructions`; the voice prompt cannot be used."},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(repo: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", repo.split("/")[-1].lower()).strip("-")


class Catalog:
    def __init__(self, settings: Settings, registry: dict[str, ModelSpec], client: docker.DockerClient):
        self.s = settings
        self.registry = registry
        self.client = client
        self.custom_path = settings.data_dir / "models.custom.yaml"
        self.cache_path = settings.data_dir / "verifications.json"
        self._lock = threading.Lock()
        self._version: str | None = None

    # ----------------------------------------------------------- registry
    def reload(self) -> None:
        fresh = load_registry(self.s.registry_path, self.custom_path)
        self.registry.clear()
        self.registry.update(fresh)

    def _read_custom(self) -> dict[str, Any]:
        if not self.custom_path.exists():
            return {"models": {}}
        data = yaml.safe_load(self.custom_path.read_text()) or {}
        data.setdefault("models", {})
        return data

    def _write_custom(self, data: dict[str, Any]) -> None:
        tmp = self.custom_path.with_suffix(".tmp")
        tmp.write_text("# Models added from the web UI. Managed by the controller.\n"
                       + yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
        tmp.replace(self.custom_path)

    def _next_port(self) -> int:
        used = {m.host_port for m in self.registry.values() if m.host_port}
        port = 8101
        while port in used:
            port += 1
        return port

    def add_model(self, repo: str, key: str, name: str, request: dict[str, Any], serve_args: list[str],
                  notes: str = "", aux_repos: list[str] | None = None) -> ModelSpec:
        record = self.cached(repo)
        if record is None:
            raise ValueError(f"verify {repo} against {self.s.backend_image} first")
        if not record["verdict"]["addable"]:
            raise ValueError("verification failed: " + "; ".join(record["verdict"]["messages"]))
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", key):
            raise ValueError("key must be lowercase letters, digits and dashes")
        with self._lock:
            if key in self.registry:
                raise ValueError(f"a model with key '{key}' already exists")
            if any(m.hf_repo == repo for m in self.registry.values()):
                raise ValueError(f"{repo} is already in the registry")
            data = self._read_custom()
            data["models"][key] = {
                "name": name or repo.split("/")[-1],
                "hf_repo": repo,
                "host_port": self._next_port(),
                "serve_args": serve_args,
                "request": request,
                "aux_repos": aux_repos if aux_repos is not None else [a["repo"] for a in record.get("aux_repos", [])],
                "experimental": record["verdict"]["voice_design"] != "native",
                "notes": notes or record["verdict"].get("summary", ""),
                "verified_with": {
                    "backend_image": record["backend_image"],
                    "vllm_omni_version": record["vllm_omni_version"],
                    "tts_model_type": record["probe"].get("tts_model_type"),
                    "checked_at": record["checked_at"],
                },
            }
            self._write_custom(data)
            self.reload()
        return self.registry[key]

    def remove_model(self, key: str) -> ModelSpec:
        with self._lock:
            spec = self.registry.get(key)
            if spec is None:
                raise KeyError(key)
            if spec.source != "custom":
                raise ValueError("built-in models are defined in config/models.yaml; edit that file instead")
            data = self._read_custom()
            data["models"].pop(key, None)
            self._write_custom(data)
            self.reload()
        return spec

    # -------------------------------------------------------- version info
    def vllm_omni_version(self) -> str | None:
        if self._version is None:
            try:
                out = self.client.containers.run(
                    self.s.backend_image,
                    entrypoint=["python3", "-c",
                                "import importlib.metadata as m;print('VERSION', m.version('vllm-omni'))"],
                    remove=True, stdout=True, stderr=False,
                ).decode()
                self._version = next(l.split()[1] for l in out.splitlines() if l.startswith("VERSION "))
            except Exception as e:  # noqa: BLE001
                log.warning("could not read vLLM-Omni version from %s: %s", self.s.backend_image, e)
                return None
        return self._version

    # -------------------------------------------------------- verification
    def _cache(self) -> dict[str, Any]:
        try:
            return json.loads(self.cache_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def cached(self, repo: str) -> dict[str, Any] | None:
        hit = self._cache().get(f"{self.s.backend_image}|{repo}")
        return hit if hit and hit.get("verify_version") == VERIFY_VERSION else None

    def _store(self, record: dict[str, Any]) -> None:
        with self._lock:
            cache = self._cache()
            cache[f"{record['backend_image']}|{record['repo']}"] = record
            self.cache_path.write_text(json.dumps(cache, indent=1))

    def hub_info(self, repo: str) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.s.hf_token}"} if self.s.hf_token else {}
        try:
            r = httpx.get(f"{HF_API}/{repo}", params={"blobs": "true"}, headers=headers, timeout=20)
        except httpx.HTTPError as e:
            return {"exists": None, "error": f"Hugging Face unreachable: {e}"}
        if r.status_code in (401, 404):
            return {"exists": False, "error": "repository not found (or private without HF_TOKEN)"}
        if r.status_code != 200:
            return {"exists": None, "error": f"Hugging Face API returned {r.status_code}"}
        d = r.json()
        card = d.get("cardData") or {}
        return {
            "exists": True,
            "gated": bool(d.get("gated")),
            "private": bool(d.get("private")),
            "license": card.get("license"),
            "pipeline_tag": d.get("pipeline_tag"),
            "downloads": d.get("downloads"),
            "likes": d.get("likes"),
            "last_modified": d.get("lastModified"),
            "size_bytes": sum((f.get("size") or 0) for f in d.get("siblings", [])),
            "files": len(d.get("siblings", [])),
        }

    def _run_probe(self, repo: str) -> dict[str, Any]:
        env = {"HF_HUB_OFFLINE": "0", "TRANSFORMERS_OFFLINE": "0"}
        if self.s.hf_token:
            env["HF_TOKEN"] = self.s.hf_token
        c = self.client.containers.run(
            self.s.backend_image,
            entrypoint=["python3", "/usr/local/bin/ttsbench-probe.py"],
            command=[repo],
            environment=env,
            mounts=[Mount(target="/root/.cache", source=self.s.cache_volume, type="volume")],
            labels={"ttsbench.probe": "true"},
            detach=True,
        )
        try:
            c.wait(timeout=self.s.probe_timeout_s)
            out = c.logs(stdout=True, stderr=False).decode("utf-8", "replace")
            line = next((l for l in out.splitlines() if l.startswith(PROBE_MARK)), None)
            if line is None:
                tail = c.logs(stdout=True, stderr=True, tail=30).decode("utf-8", "replace")
                return {"supported": False, "tts": False, "error": "probe produced no result", "log_tail": tail}
            return json.loads(line[len(PROBE_MARK):])
        except Exception as e:  # noqa: BLE001
            return {"supported": False, "tts": False, "error": f"probe failed: {e}"}
        finally:
            c.remove(force=True)

    @staticmethod
    def _verdict(repo: str, hub: dict[str, Any], probe: dict[str, Any]) -> tuple[dict, dict]:
        msgs: list[str] = []
        tts_type = probe.get("tts_model_type")
        cfg = probe.get("config") or {}
        voice_design = "none"
        request: dict[str, Any] = {"input": "{text}", "instructions": "{prompt}"}
        serve_args = ["--trust-remote-code"]

        if hub.get("exists") is False:
            msgs.append(hub.get("error") or "repository not found")
        if hub.get("gated"):
            msgs.append("gated repository: accept the licence on Hugging Face and set HF_TOKEN")
        if not probe.get("supported"):
            msgs.append(probe.get("error") or "not supported by this vLLM-Omni build")
        elif not probe.get("tts"):
            msgs.append(probe.get("note") or "served by vLLM-Omni, but not through /v1/audio/speech (not a TTS model)")

        if tts_type == "qwen3_tts":
            serve_args = ["--trust-remote-code", "--enforce-eager"]
            if str(cfg.get("tts_model_type", "")).replace("_", "").lower() == "voicedesign" \
                    or re.search(r"voice[-_.]?design$", repo.lower()):
                voice_design = "native"
                request = {"input": "{text}", "task_type": "VoiceDesign", "instructions": "{prompt}",
                           "language": "Auto"}
            else:
                voice_design = "instructions"
                msgs.append("Qwen3-TTS non-VoiceDesign checkpoint: `instructions` only steer style; "
                            "use the -VoiceDesign checkpoint for voice design")
        elif tts_type == "moss_tts" and "voicegenerator" in repo.lower().replace("-", ""):
            voice_design = "native"
        elif tts_type in ADAPTER_PRESETS:
            preset = ADAPTER_PRESETS[tts_type]
            voice_design, request = preset["voice_design"], dict(preset["request"])
            if preset.get("message"):
                msgs.append(preset["message"])
        elif tts_type:
            voice_design = "instructions" if probe.get("adapter_reads_instructions") else "none"
            if voice_design == "none":
                msgs.append("the adapter ignores `instructions`; the voice prompt will have no effect")
                request = {"input": "{text}"}

        addable = bool(probe.get("supported") and probe.get("tts") and hub.get("exists") is not False)
        status = "error" if not addable else ("ok" if voice_design == "native" and not msgs else "warning")
        # (auxiliary-repo notes are appended by verify(); they do not lower the status)
        summary = {
            "native": "Documented voice design.",
            "instructions": "Accepts `instructions`; voice design from prompts is unverified.",
            "text_prefix": "Voice description is prefixed to the input text; unverified.",
            "none": "No way to pass a voice prompt.",
        }[voice_design]
        verdict = {"addable": addable, "status": status, "voice_design": voice_design, "messages": msgs,
                   "summary": summary}
        suggestion = {"key": slugify(repo), "name": repo.split("/")[-1], "request": request,
                      "serve_args": serve_args}
        return verdict, suggestion

    def verify(self, repo: str, refresh: bool = False) -> dict[str, Any]:
        repo = repo.strip().strip("/")
        if repo.startswith("https://huggingface.co/"):
            repo = "/".join(repo.removeprefix("https://huggingface.co/").split("/")[:2])
        if not REPO_RE.match(repo):
            raise ValueError("expected a Hugging Face repo id like org/model")
        if not refresh and (hit := self.cached(repo)):
            return {**hit, "cached": True}
        hub = self.hub_info(repo)
        probe = self._run_probe(repo) if hub.get("exists") is not False else {
            "supported": False, "tts": False, "error": "skipped: repository not found"}
        aux = []
        for cand in probe.get("aux_repo_candidates") or []:
            info = self.hub_info(cand)
            if info.get("exists"):
                aux.append({"repo": cand, "size_bytes": info.get("size_bytes"), "gated": info.get("gated")})
        verdict, suggestion = self._verdict(repo, hub, probe)
        suggestion["aux_repos"] = [a["repo"] for a in aux]
        if aux:
            verdict["messages"].append(
                "also needs " + ", ".join(a["repo"] for a in aux) + " at runtime; it is downloaded with the model")
        total = (hub.get("size_bytes") or 0) + sum(a["size_bytes"] or 0 for a in aux)
        record = {
            "repo": repo,
            "verify_version": VERIFY_VERSION,
            "checked_at": _now(),
            "backend_image": self.s.backend_image,
            "vllm_omni_version": probe.get("vllm_omni_version") or self.vllm_omni_version(),
            "hub": hub,
            "aux_repos": aux,
            "total_bytes": total,
            "probe": probe,
            "verdict": verdict,
            "suggestion": suggestion,
        }
        self._store(record)
        return {**record, "cached": False}
