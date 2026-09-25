

```bash
cat > ~/.ssh/id_ed25519
```

```bash
chmod 600 ~/.ssh/id_ed25519
ssh -i ~/.ssh/id_ed25519 -T git@github.com
```


```bash
mkdir -p /workspace/.cache/huggingface
export HF_HOME=/workspace/.cache/huggingface
export HUGGINGFACE_HUB_CACHE=/workspace/.cache/huggingface/hub
export TRANSFORMERS_CACHE=/workspace/.cache/huggingface/transformers

rm -rf /root/.cache/huggingface
```

```bash
pip install "bitsandbytes>=0.39.0"
pip install flash-attn --no-build-isolation

pip uninstall -y torch torchvision torchaudio
pip install --index-url https://download.pytorch.org/whl/cu124 torch torchvision torchaudio
pip install -U peft bitsandbytes
pip install "accelerate==1.11.0" "transformers==5.2.0"
cd /workspace/LLaMA-Factory
pip install -e ".[torch,metrics]"
```

```bash
cp -r /workspace/.cache/huggingface/hub/models--Qwen--Qwen3.5-35B-A3B/snapshots/ec2d4ece1ffb563322cbee9a48fe0e3fcbce0307 \
      /root/.cache/huggingface/hub/models--Qwen--Qwen3.5-35B-A3B/snapshots/
export CUDA_LAUNCH_BLOCKING=1
```

# Lancer le finetuning en mode persistant sur mon terminal local
```bash
# se connecter d'abord au pod en mode ssh
apt update && apt install -y tmux
tmux new -s train
```

# Tester le modèle finetuné sur le pod

```bash
cd /workspace/LLaMA-Factory
llamafactory-cli chat /workspace/queryrewriter-train/configs/inference/qwen3_5_9b_rewriter_infer.yaml
```

Le fichier /workspace/queryrewriter-train/configs/inference/qwen3_5_9b_rewriter_infer.yaml :

```yaml
model_name_or_path: Qwen/Qwen3.5-9B
adapter_name_or_path: /workspace/queryrewriter-train/saves/qwen3_5_9b_rewriter_qlora/checkpoint-800
template: qwen
infer_backend: huggingface
finetuning_type: lora
```

