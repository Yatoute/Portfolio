#!/usr/bin/env bash
set -euo pipefail

# export HF_HOME=/workspace/.cache/huggingface
# export HUGGINGFACE_HUB_CACHE=/workspace/.cache/huggingface/hub
# export TRANSFORMERS_CACHE=/workspace/.cache/huggingface/transformers

# mkdir -p "$HF_HOME" "$HUGGINGFACE_HUB_CACHE" "$TRANSFORMERS_CACHE"

python -m src.training \
  --config configs/llamafactory/qwen3_rewriter_qlora.yaml \
  --workdir /workspace/LLaMA-Factory

# cp -r /workspace/.cache/huggingface/hub/models--Qwen--Qwen3.5-35B-A3B/snapshots/ec2d4ece1ffb563322cbee9a48fe0e3fcbce0307 \
#       /root/.cache/huggingface/hub/models--Qwen--Qwen3.5-35B-A3B/snapshots/
