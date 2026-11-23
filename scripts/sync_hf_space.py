"""Synchronize the standalone Hugging Face Space with the canonical app sources."""

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHARED_PATHS = (
    "backend/__init__.py",
    "backend/.env.example",
    "backend/auth.py",
    "backend/db.py",
    "backend/livekit_bridge.py",
    "backend/main.py",
    "backend/models.py",
    "backend/order_routing.py",
    "backend/pdf_export.py",
    "backend/requirements.txt",
    "backend/soap_generator.py",
    "backend/transcription.py",
    "frontend/app.js",
    "frontend/auth.js",
    "frontend/favicon.svg",
    "frontend/index.html",
    "frontend/lab.html",
    "frontend/pharmacy.html",
    "frontend/style.css",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sync",
        action="store_true",
        help="copy canonical files into hf-space instead of checking for drift",
    )
    args = parser.parse_args()

    mismatches = []
    for relative_path in SHARED_PATHS:
        source = ROOT / relative_path
        destination = ROOT / "hf-space" / relative_path
        if args.sync:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        elif not destination.exists() or source.read_bytes() != destination.read_bytes():
            mismatches.append(relative_path)

    if mismatches:
        print("Hugging Face Space files have drifted from canonical sources:")
        print("\n".join(f"  {path}" for path in mismatches))
        print("Run: python scripts/sync_hf_space.py --sync")
        return 1

    print("Hugging Face Space sources are synchronized.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
