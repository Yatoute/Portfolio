#!/usr/bin/env bash
set -euo pipefail

WORKSPACE=/workspace
PROJECT_DIR=$WORKSPACE/qwen_rewriter_ft
LLAMA_FACTORY_DIR=$WORKSPACE/LLaMA-Factory

echo "=== GPU check ==="
nvidia-smi

echo "=== Python check ==="
python --version
pip --version

echo "=== Upgrade pip tools ==="
python -m pip install --upgrade pip setuptools wheel

echo "=== Clone repositories ==="
cd "$WORKSPACE"

if [ ! -d "$LLAMA_FACTORY_DIR" ]; then
  git clone https://github.com/hiyouga/LLaMA-Factory.git
fi

if [ ! -d "$PROJECT_DIR" ]; then
  git clone git@github.com:Yatoute/queryrewriter-train.git
fi

echo "=== Install LLaMA-Factory ==="
cd "$LLAMA_FACTORY_DIR"
pip install -e ".[torch,metrics]"

echo "=== Install project requirements ==="
cd "$PROJECT_DIR"

if [ -f requirements.txt ]; then
  pip install -r requirements.txt
else
  echo "ERROR: requirements.txt not found"
  exit 1
fi

echo "=== Create output directories ==="
mkdir -p "$PROJECT_DIR/outputs"
mkdir -p "$PROJECT_DIR/saves"

echo "=== Torch CUDA sanity check ==="
python - <<'PY'
import torch
print("torch:", torch.__version__)
print("cuda available:", torch.cuda.is_available())
if torch.cuda.is_available():
    print("device:", torch.cuda.get_device_name(0))
PY

echo "=== Split dataset ==="
cd "$PROJECT_DIR"
bash scripts/split_data.sh

echo "=== Check split outputs ==="
ls -lh dataset/train.json dataset/test.json dataset/stats.json

echo "=== Launch training ==="
bash scripts/train.sh