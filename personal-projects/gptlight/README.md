# GPT-Light

**A GPT-2–style Language Model implemented from scratch in PyTorch**

GPT-Light is a fully self-contained, modular implementation of a **GPT-2–like decoder-only Transformer**.
This project aims to provide a clean, readable, and educational codebase for understanding :

* how a GPT model is built internally (multi-head attention, causal masking, MLP, LayerNorm, residuals)
* autoregressive pretraining
* decoding strategies (temperature, top-k sampling)
* loading pretrained OpenAI GPT-2 weights
* supervised fine-tuning (e.g., spam classification)