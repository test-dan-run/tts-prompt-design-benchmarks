"""Download TTS weights into the models volume (/models/<org>/<repo>).

    docker compose run --rm downloader Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign
    docker compose run --rm downloader --key qwen3-tts-voicedesign
    docker compose run --rm downloader --all          # everything in models.yaml
    docker compose run --rm downloader --list
"""
import argparse
import os
import sys
from pathlib import Path

import yaml
from huggingface_hub import snapshot_download

MODELS_DIR = Path(os.getenv("TTSBENCH_MODELS_DIR", "/models"))
REGISTRY = Path(os.getenv("TTSBENCH_REGISTRY", "/config/models.yaml"))


def registry() -> dict:
    return (yaml.safe_load(REGISTRY.read_text()) or {}).get("models", {}) if REGISTRY.exists() else {}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repos", nargs="*", help="Hugging Face repo ids")
    ap.add_argument("--key", action="append", default=[], help="registry key from models.yaml")
    ap.add_argument("--all", action="store_true", help="download every model in models.yaml")
    ap.add_argument("--list", action="store_true", help="show registry models and download state")
    ap.add_argument("--revision", default=None)
    args = ap.parse_args()

    reg = registry()
    if args.list:
        for key, cfg in reg.items():
            path = MODELS_DIR / cfg["hf_repo"]
            state = "present" if path.is_dir() and any(path.iterdir()) else "missing"
            print(f"{key:28s} {cfg['hf_repo']:45s} {state}")
        return 0

    repos = list(args.repos)
    repos += [reg[k]["hf_repo"] for k in args.key]
    if args.all:
        repos += [cfg["hf_repo"] for cfg in reg.values()]
    if not repos:
        ap.print_help()
        return 2

    for repo in dict.fromkeys(repos):
        target = MODELS_DIR / repo
        print(f"==> {repo} -> {target}", flush=True)
        snapshot_download(repo_id=repo, local_dir=target, revision=args.revision, token=os.getenv("HF_TOKEN") or None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
