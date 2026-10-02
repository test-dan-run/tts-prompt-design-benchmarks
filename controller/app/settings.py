"""Runtime settings (environment) and the model registry (YAML)."""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    registry_path: Path = Path(os.getenv("TTSBENCH_REGISTRY", "/config/models.yaml"))
    data_dir: Path = Path(os.getenv("TTSBENCH_DATA_DIR", "/data"))
    # Where the controller itself sees the models volume (read-only), used to
    # report whether a model has been downloaded.
    models_dir: Path = Path(os.getenv("TTSBENCH_MODELS_DIR", "/models"))
    # What the Docker daemon mounts into backends: a named volume or an
    # absolute host path.
    models_source: str = os.getenv("TTSBENCH_MODELS_SOURCE", "ttsbench-models")
    cache_volume: str = os.getenv("TTSBENCH_CACHE_VOLUME", "ttsbench-backend-cache")
    backend_image: str = os.getenv("TTSBENCH_BACKEND_IMAGE", "ttsbench/vllm-omni:latest")
    network: str = os.getenv("TTSBENCH_NETWORK", "ttsbench")
    container_prefix: str = os.getenv("TTSBENCH_CONTAINER_PREFIX", "ttsbench-backend-")
    publish_host: str = os.getenv("TTSBENCH_BACKEND_BIND", "127.0.0.1")
    default_gpus: str = os.getenv("TTSBENCH_BACKEND_GPUS", "all")
    exclusive: bool = _env_bool("TTSBENCH_EXCLUSIVE_BACKENDS", True)
    shm_size: str = os.getenv("TTSBENCH_BACKEND_SHM", "8g")
    hf_token: str = os.getenv("HF_TOKEN", "")
    synth_timeout_s: float = float(os.getenv("TTSBENCH_SYNTH_TIMEOUT_S", "600"))
    # Send one discarded request after load so the first stored clip is not a
    # cold start (which inflates latency / RTF several-fold).
    warmup: bool = _env_bool("TTSBENCH_WARMUP", True)


@dataclass
class ModelSpec:
    key: str
    name: str
    hf_repo: str
    host_port: int | None
    container_port: int
    startup_timeout_s: float
    serve_args: list[str]
    env: dict[str, str]
    gpus: str | None
    request: dict[str, Any]
    experimental: bool = False
    notes: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def model_path(self) -> str:
        return f"/models/{self.hf_repo}"

    def public(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "hf_repo": self.hf_repo,
            "host_port": self.host_port,
            "experimental": self.experimental,
            "notes": self.notes,
            "request_template": self.request,
        }


def load_registry(path: Path) -> dict[str, ModelSpec]:
    raw = yaml.safe_load(path.read_text()) or {}
    defaults = raw.get("defaults", {}) or {}
    specs: dict[str, ModelSpec] = {}
    for key, cfg in (raw.get("models") or {}).items():
        cfg = cfg or {}
        if "hf_repo" not in cfg:
            raise ValueError(f"model '{key}' is missing 'hf_repo'")
        env = {**(defaults.get("env") or {}), **(cfg.get("env") or {})}
        specs[key] = ModelSpec(
            key=key,
            name=cfg.get("name", key),
            hf_repo=cfg["hf_repo"],
            host_port=cfg.get("host_port"),
            container_port=int(cfg.get("container_port", defaults.get("container_port", 8091))),
            startup_timeout_s=float(cfg.get("startup_timeout_s", defaults.get("startup_timeout_s", 1800))),
            serve_args=[str(a) for a in cfg.get("serve_args", defaults.get("serve_args", []))],
            env={k: str(v) for k, v in env.items()},
            gpus=cfg.get("gpus"),
            request=copy.deepcopy(cfg.get("request") or {"input": "{text}", "instructions": "{prompt}"}),
            experimental=bool(cfg.get("experimental", False)),
            notes=(cfg.get("notes") or "").strip(),
            extra={k: v for k, v in cfg.items() if k not in ModelSpec.__dataclass_fields__},
        )
    return specs


def render_request(spec: ModelSpec, prompt: str, text: str, overrides: dict[str, Any] | None) -> dict[str, Any]:
    """Fill the request template with the voice prompt and the text to speak."""

    def fill(value: Any) -> Any:
        if isinstance(value, str):
            # str.replace, not str.format: user text may contain braces.
            return value.replace("{prompt}", prompt).replace("{text}", text)
        if isinstance(value, dict):
            return {k: fill(v) for k, v in value.items()}
        if isinstance(value, list):
            return [fill(v) for v in value]
        return value

    body = fill(copy.deepcopy(spec.request))
    body.setdefault("model", spec.hf_repo)
    body.setdefault("response_format", "wav")
    if overrides:
        body.update(overrides)
    return body
