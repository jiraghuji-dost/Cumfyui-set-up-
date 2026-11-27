#!/usr/bin/env python3
"""Run the finite ComfyUI batch configured for this Kaggle kernel."""

import subprocess
from pathlib import Path


START_SCRIPT = Path("/opt/ComfyUI/start.sh")


def main():
    if not START_SCRIPT.is_file():
        raise RuntimeError(
            "The configured Kaggle kernel must use the My AIAgent ComfyUI Docker image."
        )
    subprocess.run(["bash", str(START_SCRIPT)], check=True)


if __name__ == "__main__":
    main()
