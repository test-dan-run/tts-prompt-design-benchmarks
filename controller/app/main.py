"""TTS benchmark controller.

* lists the TTS models in the registry and their container state,
* spins vLLM-Omni backend containers up / down on demand (one at a time by default),
* generates voice-design clips and stores them in a library keyed by
  sample (voice prompt + text) and model, so clips from models that were never
  loaded at the same time can still be compared side by side.
"""
from __future__ import annotations

import csv
import io
import logging
import time
from pathlib import Path
from typing import Any

import httpx
import yaml
from fastapi import FastAPI, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field

from .audio import wav_info
from .backends import BackendError, BackendManager
from .library import Library
from .settings import Settings, load_registry, render_request

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

settings = Settings()
registry = load_registry(settings.registry_path)
manager = BackendManager(settings, registry)
library = Library(settings.data_dir)
SUITES_DIR = Path("/benchmarks")

app = FastAPI(title="TTS Prompt-Design Benchmark Controller", version="0.2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

CONTENT_TYPES = {"wav": "audio/wav", "mp3": "audio/mpeg", "flac": "audio/flac", "opus": "audio/ogg", "pcm": "audio/L16"}


def _unknown_model(key: str) -> HTTPException:
    return HTTPException(404, f"unknown model '{key}'. Known: {', '.join(registry)}")


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "models": len(registry)}


# --------------------------------------------------------------------- models
@app.get("/models")
async def list_models() -> list[dict[str, Any]]:
    """Registry models with backend state and library stats, plus models that
    only exist in the library (removed from the registry since)."""
    summary = library.model_summary()
    out = []
    for st in await run_in_threadpool(manager.list, True):
        out.append({**st, "in_registry": True, "library": summary.get(st["key"], {"clips": 0, "samples": 0})})
    for key, stats in summary.items():
        if key not in registry:
            out.append({"key": key, "name": stats.get("model_name") or key, "state": "unavailable",
                        "in_registry": False, "library": stats})
    return out


@app.get("/backends")
async def list_backends(probe: bool = True) -> list[dict[str, Any]]:
    return await run_in_threadpool(manager.list, probe)


@app.get("/backends/{key}")
async def get_backend(key: str) -> dict[str, Any]:
    try:
        return await run_in_threadpool(manager.status, key)
    except KeyError:
        raise _unknown_model(key)


@app.post("/backends/{key}/start")
async def start_backend(key: str, wait: bool = False, timeout: float | None = None) -> dict[str, Any]:
    try:
        st = await run_in_threadpool(manager.start, key)
        if wait:
            st = await run_in_threadpool(manager.wait_ready, key, timeout)
        return st
    except KeyError:
        raise _unknown_model(key)
    except BackendError as e:
        raise HTTPException(409, str(e))


@app.post("/backends/{key}/stop")
async def stop_backend(key: str) -> dict[str, Any]:
    try:
        return await run_in_threadpool(manager.stop, key)
    except KeyError:
        raise _unknown_model(key)


@app.post("/backends/stop-all")
async def stop_all() -> dict[str, str]:
    await run_in_threadpool(manager.stop_all)
    return {"status": "stopped"}


@app.get("/backends/{key}/logs", response_class=PlainTextResponse)
async def backend_logs(key: str, tail: int = 200) -> str:
    try:
        manager.spec(key)
    except KeyError:
        raise _unknown_model(key)
    return await run_in_threadpool(manager.logs, key, tail)


# ------------------------------------------------------------------ generation
async def _generate(sample: dict[str, Any], model: str, params: dict[str, Any], auto_start: bool,
                    tag: str | None, store: bool = True) -> dict[str, Any]:
    try:
        spec = manager.spec(model)
    except KeyError:
        raise _unknown_model(model)
    try:
        if auto_start:
            await run_in_threadpool(manager.start, model)
            await run_in_threadpool(manager.wait_ready, model, None)
        else:
            st = await run_in_threadpool(manager.status, model)
            if st["state"] != "ready":
                raise HTTPException(409, f"backend '{model}' is {st['state']}; start it first")
    except BackendError as e:
        raise HTTPException(409, str(e))

    body = render_request(spec, sample["prompt"], sample["text"], params)
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=settings.synth_timeout_s) as client:
            resp = await client.post(f"{manager.base_url(spec)}/v1/audio/speech", json=body)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"backend request failed: {e}")
    latency = time.perf_counter() - t0
    if resp.status_code != 200:
        raise HTTPException(502, f"backend returned {resp.status_code}: {resp.text[:2000]}")

    fmt = str(body.get("response_format", "wav"))
    info = wav_info(resp.content) if fmt == "wav" else None
    duration = info["duration_s"] if info else None
    meta = {
        "model_name": spec.name,
        "hf_repo": spec.hf_repo,
        "backend_image": settings.backend_image,
        "request": body,
        "latency_s": round(latency, 4),
        "audio_duration_s": round(duration, 4) if duration else None,
        "rtf": round(latency / duration, 4) if duration else None,
        "sample_rate": info["sample_rate"] if info else None,
        "usage": {k: v for k, v in resp.headers.items() if k.lower().startswith("x-vllm-omni-")},
        "tag": tag,
    }
    if not store:
        return {**meta, "id": None, "model": model, "sample": sample}
    clip = await run_in_threadpool(library.add_clip, sample["id"], model, resp.content, fmt, meta)
    return {**clip, "sample": sample}


class SampleIn(BaseModel):
    prompt: str = Field("", description="Natural-language description of the speaker / voice")
    text: str = Field(..., min_length=1, description="What the speaker should say")
    label: str | None = None


class GenerateIn(BaseModel):
    model: str
    params: dict[str, Any] = Field(default_factory=dict, description="Extra/override /v1/audio/speech fields")
    auto_start: bool = False
    tag: str | None = None
    store: bool = Field(True, description="False runs the request (e.g. warmup) without keeping the clip")


class SynthesisRequest(SampleIn, GenerateIn):
    pass


@app.post("/synthesize")
async def synthesize(req: SynthesisRequest) -> dict[str, Any]:
    """One-shot: find-or-create the sample, then generate a clip for it."""
    sample = await run_in_threadpool(library.upsert_sample, req.prompt, req.text, req.label or req.tag)
    return await _generate(sample, req.model, req.params, req.auto_start, req.tag, req.store)


# --------------------------------------------------------------------- samples
@app.get("/samples")
def list_samples() -> list[dict[str, Any]]:
    return library.list_samples(with_clips=True)


@app.post("/samples")
def create_sample(s: SampleIn) -> dict[str, Any]:
    return library.upsert_sample(s.prompt, s.text, s.label)


@app.post("/samples/bulk")
def create_samples(items: list[SampleIn]) -> list[dict[str, Any]]:
    return [library.upsert_sample(s.prompt, s.text, s.label) for s in items]


class SamplePatch(BaseModel):
    label: str | None = None


@app.patch("/samples/{sample_id}")
def patch_sample(sample_id: str, patch: SamplePatch) -> dict[str, Any]:
    sample = library.update_sample(sample_id, patch.label)
    if not sample:
        raise HTTPException(404, "sample not found")
    return sample


@app.delete("/samples/{sample_id}")
def delete_sample(sample_id: str) -> dict[str, str]:
    if not library.delete_sample(sample_id):
        raise HTTPException(404, "sample not found")
    return {"status": "deleted"}


@app.post("/samples/{sample_id}/generate")
async def generate_for_sample(sample_id: str, req: GenerateIn) -> dict[str, Any]:
    sample = library.get_sample(sample_id)
    if not sample:
        raise HTTPException(404, "sample not found")
    return await _generate(sample, req.model, req.params, req.auto_start, req.tag, req.store)


# ----------------------------------------------------------------------- clips
@app.get("/clips")
def list_clips(model: str | None = None, sample_id: str | None = None,
               limit: int = Query(500, ge=1, le=100000)) -> list[dict[str, Any]]:
    return library.list_clips(model=model, sample_id=sample_id, limit=limit)


@app.get("/clips.csv")
def export_clips() -> Response:
    samples = {s["id"]: s for s in library.list_samples(with_clips=False)}
    cols = ["clip_id", "model", "hf_repo", "backend_image", "created_at", "sample_id", "label", "prompt", "text",
            "latency_s", "audio_duration_s", "rtf", "sample_rate", "file"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for c in library.list_clips():
        s = samples.get(c["sample_id"], {})
        w.writerow([c["id"], c["model"], c["hf_repo"], c["backend_image"], c["created_at"], c["sample_id"],
                    s.get("label"), s.get("prompt"), s.get("text"), c["latency_s"], c["audio_duration_s"],
                    c["rtf"], c["sample_rate"], c["file"]])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=ttsbench-clips.csv"})


@app.get("/clips/{clip_id}")
def get_clip(clip_id: str) -> dict[str, Any]:
    clip = library.get_clip(clip_id)
    if not clip:
        raise HTTPException(404, "clip not found")
    return clip


@app.get("/clips/{clip_id}/audio")
def get_clip_audio(clip_id: str) -> FileResponse:
    clip = library.get_clip(clip_id)
    if not clip:
        raise HTTPException(404, "clip not found")
    path = library.clip_path(clip)
    if not path.is_file():
        raise HTTPException(410, "audio file missing from the data volume")
    return FileResponse(path, media_type=CONTENT_TYPES.get(clip["format"], "application/octet-stream"),
                        filename=f"{clip['model']}-{clip['id']}.{clip['format']}")


@app.delete("/clips/{clip_id}")
def delete_clip(clip_id: str) -> dict[str, str]:
    if not library.delete_clip(clip_id):
        raise HTTPException(404, "clip not found")
    return {"status": "deleted"}


# ---------------------------------------------------------------------- suites
@app.get("/suites")
def list_suites() -> list[dict[str, Any]]:
    if not SUITES_DIR.is_dir():
        return []
    out = []
    for p in sorted(SUITES_DIR.glob("*.y*ml")):
        cases = (yaml.safe_load(p.read_text()) or {}).get("cases", [])
        out.append({"name": p.stem, "cases": len(cases)})
    return out


@app.post("/suites/{name}/import")
def import_suite(name: str) -> list[dict[str, Any]]:
    path = next((p for p in SUITES_DIR.glob(f"{name}.y*ml") if p.stem == name), None)
    if path is None:
        raise HTTPException(404, "suite not found")
    cases = (yaml.safe_load(path.read_text()) or {}).get("cases", [])
    return [library.upsert_sample(c["prompt"], c["text"], c.get("id")) for c in cases]
