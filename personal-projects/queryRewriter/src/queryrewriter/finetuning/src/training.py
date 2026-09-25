from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        default="configs/llamafactory/qwen3_rewriter_qlora.yaml",
        help="Path to LLaMA-Factory YAML config",
    )
    parser.add_argument(
        "--workdir",
        default=".",
        help="Project root where llamafactory-cli will be executed",
    )
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    workdir = Path(args.workdir).resolve()

    cmd = [
        "llamafactory-cli",
        "train",
        str(config_path),
    ]

    print("Running:", " ".join(cmd))
    subprocess.run(cmd, cwd=workdir, check=True)


if __name__ == "__main__":
    main()
