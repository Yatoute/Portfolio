#!/usr/bin/env bash
set -euo pipefail

python -m src.train_test_split \
  --input dataset/dataset_sft.jsonl \
  --output-dir dataset \
  --test-size 0.1 \
  --seed 42