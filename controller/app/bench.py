"""Batch benchmark: run a suite of (voice prompt, text) cases across models.

    docker compose run --rm bench /benchmarks/suite.yaml [--models a,b] [--repeats N] [--keep-running]

Talks to the controller API, so every clip lands in the shared library and
shows up in the web UI's comparison grid. Writes a CSV summary to /data/bench/.
"""
from __future__ import annotations

import argparse
import csv
import os
import statistics
import sys
from datetime import datetime
from pathlib import Path

import httpx
import yaml


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("suite", type=Path)
    ap.add_argument("--controller", default=os.getenv("TTSBENCH_CONTROLLER_URL", "http://controller:8090"))
    ap.add_argument("--models", help="comma-separated registry keys (default: suite `models`, else all)")
    ap.add_argument("--repeats", type=int, help="override suite `repeats`")
    ap.add_argument("--keep-running", action="store_true", help="do not stop backends afterwards")
    ap.add_argument("--out", type=Path, default=Path(os.getenv("TTSBENCH_DATA_DIR", "/data")) / "bench")
    args = ap.parse_args()

    suite = yaml.safe_load(args.suite.read_text())
    cases = suite["cases"]
    repeats = args.repeats or int(suite.get("repeats", 1))
    client = httpx.Client(base_url=args.controller, timeout=None)
    known = [b["key"] for b in client.get("/backends", params={"probe": False}).json()]
    models = args.models.split(",") if args.models else (suite.get("models") or known)

    args.out.mkdir(parents=True, exist_ok=True)
    out = args.out / f"{args.suite.stem}-{datetime.now():%Y%m%dT%H%M%S}.csv"
    fields = ["model", "case", "repeat", "clip_id", "latency_s", "audio_duration_s", "rtf", "audio_url", "error"]
    summary: dict[str, list[float]] = {}

    with out.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for model in models:
            print(f"== {model}: starting backend (may take a while on first load)", flush=True)
            r = client.post(f"/backends/{model}/start", params={"wait": True})
            if r.status_code != 200:
                print(f"   skipped: {r.text}", flush=True)
                writer.writerow({"model": model, "error": r.text[:500]})
                continue
            if suite.get("warmup", True):
                client.post("/synthesize", json={"model": model, "prompt": cases[0]["prompt"],
                                                 "text": cases[0]["text"], "tag": "warmup", "store": False})
            for case in cases:
                for i in range(repeats):
                    r = client.post("/synthesize", json={
                        "model": model, "prompt": case["prompt"], "text": case["text"],
                        "params": case.get("params", {}), "tag": case["id"], "label": case["id"],
                    })
                    row = {"model": model, "case": case["id"], "repeat": i}
                    if r.status_code == 200:
                        rec = r.json()
                        row.update({k: rec.get(k) for k in ("latency_s", "audio_duration_s", "rtf", "audio_url")})
                        row["clip_id"] = rec["id"]
                        if rec.get("rtf") is not None:
                            summary.setdefault(model, []).append(rec["rtf"])
                    else:
                        row["error"] = r.text[:500]
                    writer.writerow(row)
                    print(f"   {case['id']} #{i}: {row.get('latency_s')}s latency, RTF {row.get('rtf')}"
                          + (f"  ERROR {row['error']}" if row.get("error") else ""), flush=True)
            if not args.keep_running:
                client.post(f"/backends/{model}/stop")

    print(f"\nCSV written to {out}")
    for model, rtfs in summary.items():
        print(f"{model:32s} median RTF {statistics.median(rtfs):.3f}  (n={len(rtfs)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
