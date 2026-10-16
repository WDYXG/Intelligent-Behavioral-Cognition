"""Explicit CLI entry; importing this module never starts an experiment."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from runner import run_experiment


def main() -> int:
    parser = argparse.ArgumentParser(description="Load all twelve authorized Personas and run the configured SceneAdapter with the shared Runner. Real API; no offline fallback.")
    parser.add_argument("--config", required=True, type=Path, help="Scene/framework JSON configuration")
    args = parser.parse_args()
    directory, manifest = run_experiment(args.config.resolve())
    print(json.dumps({"status": manifest["status"], "output_directory": str(directory),
                      "runs": len(manifest["runs"]), "error": manifest.get("error")}, ensure_ascii=False, indent=2))
    return 0 if manifest["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
