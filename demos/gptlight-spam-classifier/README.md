# gptlight — spam classifier demo

A GPT-2 (124M) implemented from scratch in PyTorch, loaded with the official
OpenAI GPT-2 checkpoint (manually mapped from the original TensorFlow
format — QKV split, Conv1D→Linear transpose, weight tying), then
fine-tuned on the SMS Spam Collection dataset.

Test set accuracy: **96.96%** (chance-level ~48% before fine-tuning).

Full write-up and training notebooks: https://github.com/Yatoute/gpt-light

## Deploying (Fly.io)

Hugging Face Spaces now requires a PRO subscription to host Gradio/Docker
Spaces even on the free CPU tier, so this runs on Fly.io instead — same
pattern as the other two demos in this portfolio.

```bash
cd demos/gptlight-spam-classifier
flyctl launch --no-deploy
flyctl deploy
```

No persistent volume needed (the checkpoint is baked into the image, and
there's no user data to retain between requests).
