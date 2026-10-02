"""Check whether a Hugging Face model is servable by the vLLM-Omni build in
this image, and whether it is exposed through /v1/audio/speech.

    python probe.py <hf_repo_or_local_path>

Uses vLLM-Omni's own resolution code (the same path `vllm serve --omni` takes)
and only fetches config files, never weights. No GPU needed. The result is a
single JSON line prefixed with "TTSBENCH_PROBE ", since vLLM logs to stdout.
"""
import inspect
import json
import os
import re
import sys
import traceback

# Hugging Face repos that model code loads by default (codecs, tokenizers...).
# With HF_HUB_OFFLINE=1 these must be downloaded alongside the main weights.
AUX_PATTERNS = [
    r'getattr\([^()]*?,\s*["\'][^"\']*(?:path|name|repo|id)[^"\']*["\']\s*,\s*["\']([\w.-]+/[\w.-]+)["\']\s*\)',
    r'\.get\(\s*["\'][^"\']*(?:path|name|repo|id)[^"\']*["\']\s*,\s*["\']([\w.-]+/[\w.-]+)["\']\s*\)',
    r'from_pretrained\(\s*["\']([\w.-]+/[\w.-]+)["\']',
    r'(?:snapshot_download|hf_hub_download)\(\s*(?:repo_id\s*=\s*)?["\']([\w.-]+/[\w.-]+)["\']',
    r'^[A-Z_]*(?:REPO|MODEL|PATH|TOKENIZER|CODEC)[A-Z_]*\s*=\s*["\']([\w.-]+/[\w.-]+)["\']',
]
CONFIG_KEY = re.compile(r"(name_or_path|_path|_repo|_repo_id)$")
REPO_VALUE = re.compile(r"^[A-Za-z0-9][\w.-]*/[\w.-]+$")


def aux_repo_candidates(repo: str, stages: list[dict], cfg: dict | None) -> list[str]:
    found: set[str] = set()
    try:
        from vllm_omni.model_executor.models import registry as reg

        table = {**getattr(reg, "_OMNI_MODELS", {}), **getattr(reg, "_VLLM_OMNI_MODELS", {})}
        base = os.path.dirname(reg.__file__)
        pkgs = {table[s["model_arch"]][0] for s in stages if s.get("model_arch") in table}
        for pkg in pkgs:
            for root, _dirs, files in os.walk(os.path.join(base, pkg)):
                for f in files:
                    if f.endswith(".py"):
                        src = open(os.path.join(root, f), encoding="utf-8", errors="replace").read()
                        for pat in AUX_PATTERNS:
                            found |= set(re.findall(pat, src, re.M))
    except Exception:  # noqa: BLE001
        pass

    def walk(obj, key=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                walk(v, k)
        # `_name_or_path` is provenance written by transformers, not a dependency.
        elif isinstance(obj, str) and key != "_name_or_path" and CONFIG_KEY.search(key) and REPO_VALUE.match(obj) and not obj.startswith("/"):
            found.add(obj)

    walk(cfg or {})
    found.discard(repo)
    return sorted(found)


def main() -> None:
    repo = sys.argv[1]
    res: dict = {"repo": repo, "supported": False, "tts": False}
    try:
        from vllm_omni.version import __version__ as omni_version
    except Exception:  # noqa: BLE001
        import vllm_omni

        omni_version = getattr(vllm_omni, "__version__", "unknown")
    res["vllm_omni_version"] = omni_version

    try:
        from transformers.utils.hub import cached_file

        path = cached_file(repo, "config.json")
        with open(path) as fh:
            cfg = json.load(fh)
        res["config"] = {k: cfg.get(k) for k in ("architectures", "model_type", "tts_model_type")}
        res["_full_config"] = cfg
    except Exception as e:  # noqa: BLE001
        res["config_error"] = f"{type(e).__name__}: {e}"[:500]

    try:
        from vllm_omni.config.resolver import resolve_omni_config

        resolution = resolve_omni_config(
            repo,
            trust_remote_code=True,
            deploy_config_path=None,
            cli_overrides={},
            stage_overrides=None,
            strategy_config_path=None,
        )
        res["supported"] = True
        res["deploy_config"] = str(resolution.config_path) if resolution.config_path else None
    except Exception as e:  # noqa: BLE001
        res["error"] = f"{type(e).__name__}: {e}"[:1000]
        res["traceback_tail"] = traceback.format_exc()[-1500:]
        res.pop("_full_config", None)
        print("TTSBENCH_PROBE " + json.dumps(res), flush=True)
        return

    try:
        from vllm_omni.entrypoints.openai.serving_speech import _stage_speech_metadata
        from vllm_omni.entrypoints.openai.tts_adapters import detect_tts_model_type, resolve_adapter

        stages, tts_type = [], None
        for st in resolution.stage_configs:
            model_stage, model_arch, worker_type = _stage_speech_metadata(st)
            stages.append({"model_stage": model_stage, "model_arch": model_arch, "worker_type": worker_type})
            tts_type = tts_type or detect_tts_model_type(model_stage, model_arch)
        res["stages"] = stages
        # Pure diffusion pipelines bypass the adapter registry: api_server wires
        # /v1/audio/speech to OmniVoice-specific handling for every one of them,
        # so only OmniVoice's own pipeline actually produces speech that way.
        if tts_type is None and stages and all(s["worker_type"] in (None, "diffusion") for s in stages):
            archs = {s["model_arch"] or "" for s in stages}
            if any(a.startswith("OmniVoice") for a in archs):
                tts_type = "omnivoice"
                res["diffusion_tts"] = True
            else:
                res["note"] = "pure diffusion pipeline; /v1/audio/speech only handles OmniVoice there"
        res["tts_model_type"] = tts_type
        res["tts"] = tts_type is not None
        res["aux_repo_candidates"] = aux_repo_candidates(repo, stages, res.get("_full_config"))
        if tts_type:
            adapter = resolve_adapter(tts_type)
            source = inspect.getsource(sys.modules[adapter.__module__])
            res["adapter"] = adapter.__qualname__
            res["adapter_reads_instructions"] = "request.instructions" in source
    except Exception as e:  # noqa: BLE001
        res["tts_detection_error"] = f"{type(e).__name__}: {e}"[:500]

    res.pop("_full_config", None)
    print("TTSBENCH_PROBE " + json.dumps(res), flush=True)


if __name__ == "__main__":
    main()
