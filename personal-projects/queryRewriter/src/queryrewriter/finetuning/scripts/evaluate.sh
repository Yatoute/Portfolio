#!/usr/bin/env bash
set -euo pipefail

python -m src.evaluate.py \
  --references datasets/test.json \
  --predictions outputs/test_predictions.jsonl
