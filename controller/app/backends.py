"""Lifecycle of vLLM-Omni backend containers via the Docker Engine API."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

import docker
import httpx
from docker.errors import APIError, ImageNotFound, NotFound
from docker.types import DeviceRequest, Mount

from .settings import ModelSpec, Settings, render_request

log = logging.getLogger("ttsbench.backends")

LABEL_MANAGED = "ttsbench.managed"
LABEL_MODEL = "ttsbench.model"


class BackendError(RuntimeError):
    pass


class BackendManager:
    def __init__(self, settings: Settings, registry: dict[str, ModelSpec]):
        self.s = settings
        self.registry = registry
        self.client = docker.from_env()
        self._lock = threading.Lock()  # serialise start/stop decisions
        self._warm_started: set[str] = set()  # container ids
        self._warm_done: set[str] = set()

    # ---------------------------------------------------------------- helpers
    def spec(self, key: str) -> ModelSpec:
        if key not in self.registry:
            raise KeyError(key)
        return self.registry[key]

    def container_name(self, key: str) -> str:
        return f"{self.s.container_prefix}{key}"

    def base_url(self, spec: ModelSpec) -> str:
        # Reached over the shared Docker network by container name.
        return f"http://{self.container_name(spec.key)}:{spec.container_port}"

    def _container(self, key: str):
        try:
            return self.client.containers.get(self.container_name(key))
        except NotFound:
            return None

    def is_downloaded(self, spec: ModelSpec) -> bool | None:
        root = self.s.models_dir
        if not root.exists():
            return None  # controller cannot see the volume; unknown
        path = root / spec.hf_repo
        if not (path.is_dir() and any(path.iterdir())):
            return False
        # huggingface_hub keeps partial files here until a download completes.
        partial = path / ".cache" / "huggingface" / "download"
        if partial.is_dir() and any(partial.rglob("*.incomplete")):
            return False
        return all(self.aux_present(repo) for repo in spec.aux_repos)

    def aux_present(self, repo: str) -> bool:
        snaps = self.s.models_dir / ".hf-cache" / f"models--{repo.replace('/', '--')}" / "snapshots"
        return snaps.is_dir() and any(snaps.iterdir())

    def _probe(self, spec: ModelSpec, timeout: float = 2.0) -> bool:
        try:
            r = httpx.get(f"{self.base_url(spec)}/health", timeout=timeout)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    # ----------------------------------------------------------------- status
    def status(self, key: str, probe: bool = True) -> dict[str, Any]:
        spec = self.spec(key)
        c = self._container(key)
        info: dict[str, Any] = {
            **spec.public(),
            "container": self.container_name(key),
            "downloaded": self.is_downloaded(spec),
            "state": "stopped",
            "api_base_internal": self.base_url(spec),
            "api_base_host": f"http://localhost:{spec.host_port}" if spec.host_port else None,
        }
        if c is None:
            return info
        c.reload()
        st = c.attrs.get("State", {})
        info["started_at"] = st.get("StartedAt")
        if st.get("Running"):
            if probe and self._probe(spec):
                info["state"] = "ready" if self._warmed(spec, c.id) else "starting"
                if info["state"] == "starting":
                    info["phase"] = "warming up"
            else:
                info["state"] = "starting"
        elif st.get("Status") in {"created", "restarting"}:
            info["state"] = "starting"
        else:
            info["state"] = "error"
            info["exit_code"] = st.get("ExitCode")
            info["error"] = st.get("Error") or None
            info["logs_tail"] = self.logs(key, tail=40)
        return info

    def _warmed(self, spec: ModelSpec, container_id: str) -> bool:
        if not self.s.warmup or container_id in self._warm_done:
            return True
        if container_id not in self._warm_started:
            self._warm_started.add(container_id)
            threading.Thread(target=self._warmup, args=(spec, container_id), daemon=True).start()
        return False

    def _warmup(self, spec: ModelSpec, container_id: str) -> None:
        body = render_request(spec, "A clear, neutral adult voice.", "Hello, this is a warm-up request.", None)
        try:
            r = httpx.post(f"{self.base_url(spec)}/v1/audio/speech", json=body, timeout=self.s.synth_timeout_s)
            log.info("warm-up for %s finished with HTTP %s", spec.key, r.status_code)
        except httpx.HTTPError as e:
            log.warning("warm-up for %s failed: %s", spec.key, e)
        finally:
            self._warm_done.add(container_id)

    def list(self, probe: bool = True) -> list[dict[str, Any]]:
        return [self.status(k, probe=probe) for k in self.registry]

    def logs(self, key: str, tail: int = 200) -> str:
        c = self._container(key)
        if c is None:
            return ""
        return c.logs(tail=tail).decode("utf-8", errors="replace")

    # -------------------------------------------------------------- lifecycle
    def _device_requests(self, spec: ModelSpec) -> list[DeviceRequest]:
        gpus = str(spec.gpus or self.s.default_gpus).strip().lower()
        if gpus in {"", "none", "cpu"}:
            return []
        if gpus == "all":
            return [DeviceRequest(count=-1, capabilities=[["gpu"]])]
        return [DeviceRequest(device_ids=[g.strip() for g in gpus.split(",")], capabilities=[["gpu"]])]

    def _mounts(self) -> list[Mount]:
        src = self.s.models_source
        models = Mount(
            target="/models",
            source=src,
            type="bind" if src.startswith("/") else "volume",
            read_only=True,
        )
        # Writable compile / kernel cache so restarts are faster. No weights here.
        cache = Mount(target="/root/.cache", source=self.s.cache_volume, type="volume")
        return [models, cache]

    def start(self, key: str) -> dict[str, Any]:
        spec = self.spec(key)
        with self._lock:
            if self.is_downloaded(spec) is False:
                raise BackendError(
                    f"weights for '{key}' not found at {self.s.models_dir / spec.hf_repo}. "
                    f"Run: docker compose run --rm downloader {spec.hf_repo}"
                )
            if self.s.exclusive:
                for other in self.registry:
                    if other != key and self._container(other) is not None:
                        log.info("exclusive mode: stopping %s before starting %s", other, key)
                        self._remove(other)

            c = self._container(key)
            if c is not None:
                c.reload()
                if c.status in {"running", "created", "restarting"}:
                    return self.status(key)
                self._remove(key)  # stale/exited container: recreate with current config

            env = {
                **spec.env,
                "MODEL_PATH": spec.model_path,
                "SERVED_MODEL_NAME": spec.hf_repo,
                "PORT": str(spec.container_port),
                # Auxiliary repos (codecs, tokenizers) resolve offline from here.
                "HF_HUB_CACHE": spec.HF_CACHE,
            }
            if self.s.hf_token:
                env["HF_TOKEN"] = self.s.hf_token
            ports = {}
            if spec.host_port:
                ports[f"{spec.container_port}/tcp"] = (self.s.publish_host, spec.host_port)
            try:
                self.client.containers.run(
                    self.s.backend_image,
                    command=spec.serve_args,
                    name=self.container_name(key),
                    detach=True,
                    environment=env,
                    mounts=self._mounts(),
                    device_requests=self._device_requests(spec),
                    ipc_mode="host",
                    shm_size=self.s.shm_size,
                    network=self.s.network,
                    ports=ports,
                    labels={LABEL_MANAGED: "true", LABEL_MODEL: key},
                )
            except ImageNotFound as e:
                raise BackendError(
                    f"backend image '{self.s.backend_image}' not found; build it with "
                    "`docker compose build backend-image`"
                ) from e
            except APIError as e:
                raise BackendError(f"docker failed to start '{key}': {e.explanation or e}") from e
        log.info("started backend %s", key)
        return self.status(key, probe=False)

    def _remove(self, key: str) -> None:
        c = self._container(key)
        if c is None:
            return
        try:
            c.stop(timeout=20)
        except APIError:
            pass
        try:
            c.remove(force=True)
        except NotFound:
            pass

    def stop(self, key: str) -> dict[str, Any]:
        self.spec(key)
        with self._lock:
            self._remove(key)
        return self.status(key, probe=False)

    def stop_all(self) -> None:
        for c in self.client.containers.list(all=True, filters={"label": f"{LABEL_MANAGED}=true"}):
            c.remove(force=True)

    def wait_ready(self, key: str, timeout: float | None = None, poll: float = 3.0) -> dict[str, Any]:
        spec = self.spec(key)
        deadline = time.monotonic() + (timeout if timeout is not None else spec.startup_timeout_s)
        while True:
            st = self.status(key)
            if st["state"] == "ready":
                return st
            if st["state"] in {"error", "stopped"}:
                raise BackendError(f"backend '{key}' is {st['state']}: {st.get('logs_tail') or ''}".strip())
            if time.monotonic() > deadline:
                raise BackendError(f"backend '{key}' not ready within {timeout}s")
            time.sleep(poll)
