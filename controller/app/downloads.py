"""Weight downloads into the models volume, run as one-shot downloader
containers so the controller itself keeps the volume read-only."""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any

import docker
from docker.errors import NotFound
from docker.types import Mount

from .settings import ModelSpec, Settings

log = logging.getLogger("ttsbench.downloads")
PREFIX = "ttsbench-download-"


def dir_bytes(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


class DownloadManager:
    def __init__(self, settings: Settings, client: docker.DockerClient):
        self.s = settings
        self.client = client
        self._expected: dict[str, int] = {}
        self._finished: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def _mount(self, read_only: bool) -> Mount:
        src = self.s.models_source
        return Mount(target="/models", source=src, type="bind" if src.startswith("/") else "volume",
                     read_only=read_only)

    def _container(self, key: str):
        try:
            return self.client.containers.get(PREFIX + key)
        except NotFound:
            return None

    def is_active(self, key: str) -> bool:
        c = self._container(key)
        return c is not None and c.status in ("created", "running")

    def start(self, spec: ModelSpec, expected_bytes: int | None = None) -> dict[str, Any]:
        with self._lock:
            c = self._container(spec.key)
            if c is not None:
                if c.status in ("created", "running"):
                    return self.status(spec)
                c.remove(force=True)
            self._finished.pop(spec.key, None)
            if expected_bytes:
                self._expected[spec.key] = expected_bytes
            env = {"HF_TOKEN": self.s.hf_token} if self.s.hf_token else {}
            self.client.containers.run(
                self.s.downloader_image,
                command=[spec.hf_repo] + [a for repo in spec.aux_repos for a in ("--aux", repo)],
                name=PREFIX + spec.key,
                environment=env,
                mounts=[self._mount(read_only=False)],
                labels={"ttsbench.download": spec.key, "ttsbench.expected_bytes": str(expected_bytes or 0)},
                detach=True,
            )
        log.info("download started for %s (%s)", spec.key, spec.hf_repo)
        return self.status(spec)

    def cancel(self, key: str) -> None:
        c = self._container(key)
        if c is not None:
            c.remove(force=True)
        self._finished[key] = {"state": "cancelled"}

    def status(self, spec: ModelSpec) -> dict[str, Any] | None:
        c = self._container(spec.key)
        if c is None:
            return self._finished.get(spec.key)
        try:
            return self._status_of(spec, c)
        except NotFound:  # a concurrent poll already collected it
            return self._finished.get(spec.key)

    def _status_of(self, spec: ModelSpec, c) -> dict[str, Any]:
        c.reload()
        done = dir_bytes(self.s.models_dir / spec.hf_repo) + sum(
            dir_bytes(self.s.models_dir / ".hf-cache" / f"models--{repo.replace('/', '--')}") for repo in spec.aux_repos
        )
        total = self._expected.get(spec.key) or int(c.labels.get("ttsbench.expected_bytes") or 0) or None
        info: dict[str, Any] = {"bytes": done, "total": total}
        if c.status in ("created", "running"):
            return {**info, "state": "running"}
        code = c.attrs.get("State", {}).get("ExitCode")
        if code == 0:
            result = {**info, "state": "done"}
        else:
            result = {**info, "state": "error", "exit_code": code,
                      "error": c.logs(tail=15).decode("utf-8", "replace")}
        c.remove(force=True)
        self._finished[spec.key] = result
        return result

    def delete_weights(self, spec: ModelSpec) -> None:
        self.cancel(spec.key)
        self.client.containers.run(
            self.s.downloader_image,
            command=["--delete", spec.hf_repo],
            mounts=[self._mount(read_only=False)],
            remove=True,
        )
        self._finished.pop(spec.key, None)
