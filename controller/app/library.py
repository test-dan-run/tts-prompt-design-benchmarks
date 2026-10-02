"""Persistent clip library.

A *sample* is one voice-design task: (voice prompt, text). A *clip* is one
audio file generated for a sample by a specific model. Clips keep the model
key, the Hugging Face repo and the backend image they came from, so results
survive switching models (only one fits on the GPU at a time) and can be
compared side by side later.

Layout on the data volume:
    /data/library.db                       SQLite index
    /data/audio/<model-key>/<clip-id>.<ext> audio files
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    id          TEXT PRIMARY KEY,
    prompt      TEXT NOT NULL,
    text        TEXT NOT NULL,
    label       TEXT,
    created_at  TEXT NOT NULL,
    UNIQUE (prompt, text)
);
CREATE TABLE IF NOT EXISTS clips (
    id               TEXT PRIMARY KEY,
    sample_id        TEXT NOT NULL REFERENCES samples(id) ON DELETE CASCADE,
    model            TEXT NOT NULL,
    model_name       TEXT,
    hf_repo          TEXT,
    backend_image    TEXT,
    created_at       TEXT NOT NULL,
    request          TEXT,
    format           TEXT NOT NULL,
    file             TEXT NOT NULL,
    bytes            INTEGER,
    latency_s        REAL,
    audio_duration_s REAL,
    rtf              REAL,
    sample_rate      INTEGER,
    usage            TEXT,
    tag              TEXT
);
CREATE INDEX IF NOT EXISTS clips_sample ON clips(sample_id);
CREATE INDEX IF NOT EXISTS clips_model  ON clips(model);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"


class Library:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.audio_dir = data_dir / "audio"
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(data_dir / "library.db", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.executescript(SCHEMA)
        self._import_legacy_jsonl()

    # One sqlite3 connection is shared by FastAPI's worker threads, so every
    # statement, reads included, runs under the lock and is fully fetched.
    def _all(self, sql: str, args: tuple | list = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.db.execute(sql, args).fetchall()

    def _one(self, sql: str, args: tuple | list = ()) -> sqlite3.Row | None:
        with self._lock:
            return self.db.execute(sql, args).fetchone()

    # ------------------------------------------------------------ samples
    def _sample_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)

    def upsert_sample(self, prompt: str, text: str, label: str | None = None) -> dict[str, Any]:
        prompt, text = prompt.strip(), text.strip()
        with self._lock, self.db:
            row = self.db.execute("SELECT * FROM samples WHERE prompt=? AND text=?", (prompt, text)).fetchone()
            if row:
                if label and not row["label"]:
                    self.db.execute("UPDATE samples SET label=? WHERE id=?", (label, row["id"]))
                    row = self.db.execute("SELECT * FROM samples WHERE id=?", (row["id"],)).fetchone()
                return self._sample_row(row)
            sid = _new_id("s-")
            self.db.execute(
                "INSERT INTO samples (id, prompt, text, label, created_at) VALUES (?,?,?,?,?)",
                (sid, prompt, text, label, _now()),
            )
            return self.get_sample(sid)  # type: ignore[return-value]

    def get_sample(self, sample_id: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM samples WHERE id=?", (sample_id,))
        return self._sample_row(row) if row else None

    def update_sample(self, sample_id: str, label: str | None) -> dict[str, Any] | None:
        with self._lock, self.db:
            self.db.execute("UPDATE samples SET label=? WHERE id=?", (label, sample_id))
        return self.get_sample(sample_id)

    def list_samples(self, with_clips: bool = True) -> list[dict[str, Any]]:
        samples = [self._sample_row(r) for r in self._all("SELECT * FROM samples ORDER BY created_at DESC")]
        if with_clips:
            by_sample: dict[str, list[dict]] = {}
            for clip in self.list_clips():
                by_sample.setdefault(clip["sample_id"], []).append(clip)
            for s in samples:
                s["clips"] = by_sample.get(s["id"], [])
        return samples

    def delete_sample(self, sample_id: str) -> bool:
        with self._lock:
            for clip in self.list_clips(sample_id=sample_id):
                self._unlink(clip)
            with self.db:
                cur = self.db.execute("DELETE FROM samples WHERE id=?", (sample_id,))
            return cur.rowcount > 0

    # --------------------------------------------------------------- clips
    def _clip_row(self, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        for key in ("request", "usage"):
            d[key] = json.loads(d[key]) if d.get(key) else None
        d["audio_url"] = f"/clips/{d['id']}/audio"
        return d

    def add_clip(self, sample_id: str, model: str, audio: bytes, fmt: str, meta: dict[str, Any]) -> dict[str, Any]:
        cid = _new_id("c-")
        rel = Path("audio") / model / f"{cid}.{fmt}"
        path = self.data_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(audio)
        with self._lock, self.db:
            self.db.execute(
                """INSERT INTO clips (id, sample_id, model, model_name, hf_repo, backend_image, created_at,
                   request, format, file, bytes, latency_s, audio_duration_s, rtf, sample_rate, usage, tag)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    cid, sample_id, model, meta.get("model_name"), meta.get("hf_repo"), meta.get("backend_image"),
                    _now(), json.dumps(meta.get("request")), fmt, str(rel), len(audio), meta.get("latency_s"),
                    meta.get("audio_duration_s"), meta.get("rtf"), meta.get("sample_rate"),
                    json.dumps(meta.get("usage") or {}), meta.get("tag"),
                ),
            )
        return self.get_clip(cid)  # type: ignore[return-value]

    def get_clip(self, clip_id: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM clips WHERE id=?", (clip_id,))
        return self._clip_row(row) if row else None

    def clip_path(self, clip: dict[str, Any]) -> Path:
        return self.data_dir / clip["file"]

    def list_clips(self, model: str | None = None, sample_id: str | None = None, limit: int | None = None) -> list[dict]:
        q, args = "SELECT * FROM clips WHERE 1=1", []
        if model:
            q += " AND model=?"
            args.append(model)
        if sample_id:
            q += " AND sample_id=?"
            args.append(sample_id)
        q += " ORDER BY created_at DESC"
        if limit:
            q += f" LIMIT {int(limit)}"
        return [self._clip_row(r) for r in self._all(q, args)]

    def _unlink(self, clip: dict[str, Any]) -> None:
        try:
            self.clip_path(clip).unlink()
        except FileNotFoundError:
            pass

    def delete_clip(self, clip_id: str) -> bool:
        with self._lock:
            clip = self.get_clip(clip_id)
            if not clip:
                return False
            self._unlink(clip)
            with self.db:
                self.db.execute("DELETE FROM clips WHERE id=?", (clip_id,))
            return True

    def model_summary(self) -> dict[str, dict[str, Any]]:
        rows = self._all(
            """SELECT model, MAX(model_name) AS model_name, COUNT(*) AS clips,
                      COUNT(DISTINCT sample_id) AS samples, AVG(rtf) AS mean_rtf
               FROM clips GROUP BY model"""
        )
        return {r["model"]: dict(r) for r in rows}

    # -------------------------------------------------------------- legacy
    def _import_legacy_jsonl(self) -> None:
        """One-off import of runs recorded before the library existed."""
        legacy = self.data_dir / "runs.jsonl"
        if not legacy.exists():
            return
        for line in legacy.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            src = self.data_dir / r.get("audio_path", "").lstrip("/")
            if not src.is_file():
                continue
            sample = self.upsert_sample(r.get("prompt", ""), r.get("text", ""), r.get("tag"))
            with self._lock, self.db:
                self.db.execute(
                    """INSERT OR IGNORE INTO clips (id, sample_id, model, model_name, hf_repo, created_at, request,
                       format, file, bytes, latency_s, audio_duration_s, rtf, sample_rate, usage, tag)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        "c-" + r["id"], sample["id"], r["model"], r.get("model_name"), r.get("hf_repo"),
                        r.get("created_at"), json.dumps(r.get("request")), r.get("format", "wav"),
                        str(src.relative_to(self.data_dir)), r.get("bytes"), r.get("latency_s"),
                        r.get("audio_duration_s"), r.get("rtf"), r.get("sample_rate"),
                        json.dumps(r.get("usage") or {}), r.get("tag"),
                    ),
                )
        legacy.rename(legacy.with_suffix(".jsonl.imported"))
