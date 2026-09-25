"""
Gradio demo for the gptlight spam classifier.

The model is a from-scratch GPT-2 (124M) implementation, loaded with the
official OpenAI GPT-2 checkpoint (manually mapped from the original
TensorFlow format), then fine-tuned on the SMS Spam Collection dataset.

See: https://github.com/Yatoute/gpt-light
"""

from __future__ import annotations

import os

import gradio as gr
import torch

from gptlight.config import GPTConfig
from gptlight.models import GPTClassifier
from gptlight.tokenizer import GPTTokenizer

CHECKPOINT_PATH = os.environ.get("CHECKPOINT_PATH", "checkpoint/gpt_spam_classifier.pt")
MAX_TRAIN_TEXT_LENGTH = 120  # longest example seen during fine-tuning
PAD_TOKEN_ID = 50256  # <|endoftext|>
MAX_INPUT_CHARS = 2000  # bound worst-case latency on the free CPU tier

_device = torch.device("cpu")


def _load_model() -> GPTClassifier:
    cfg = GPTConfig(
        vocab_size=50257,
        context_length=1024,
        emb_dim=768,
        n_heads=12,
        n_layers=12,
        drop_rate=0.0,
        qkv_bias=True,
    )
    model = GPTClassifier(cfg, num_classes=2)
    state = torch.load(CHECKPOINT_PATH, map_location=_device)
    model.load_state_dict(state["model_state"])
    model.eval()
    return model


# Loaded once at process start, not per-request.
_model = _load_model()
_tokenizer = GPTTokenizer()


@torch.no_grad()
def classify(text: str) -> dict[str, float]:
    if not text or not text.strip():
        return {"ham": 0.0, "spam": 0.0}

    text = text[:MAX_INPUT_CHARS]

    input_ids = _tokenizer.encode(text).tolist()
    supported_context_length = _model.pos_emb.weight.shape[0]
    max_length = min(supported_context_length, max(MAX_TRAIN_TEXT_LENGTH, len(input_ids)))
    input_ids = input_ids[:max_length]
    input_ids += [PAD_TOKEN_ID] * (max_length - len(input_ids))

    input_tensor = torch.tensor(input_ids, dtype=torch.long, device=_device).unsqueeze(0)
    logits = _model(input_tensor)[:, -1, :]
    probs = torch.softmax(logits, dim=-1).squeeze(0)

    return {"ham": probs[0].item(), "spam": probs[1].item()}


EXAMPLES = [
    "Congratulations! You have WON a $1000 gift card. Click here to claim now!!!",
    "Hey, are we still on for lunch tomorrow at noon?",
    "URGENT: Your account has been suspended. Verify your identity immediately by clicking this link.",
    "FREE entry in a weekly competition to win an iPad. Text WIN to 80086 now!",
]

demo = gr.Interface(
    fn=classify,
    inputs=gr.Textbox(
        label="Message (SMS ou email court)",
        placeholder="Collez un message ici...",
        lines=4,
        max_length=MAX_INPUT_CHARS,
    ),
    outputs=gr.Label(label="Prédiction", num_top_classes=2),
    examples=EXAMPLES,
    title="gptlight — Classifieur de spam (GPT-2 from scratch, fine-tuné)",
    description=(
        "GPT-2 (124M) implémenté from scratch en PyTorch, chargé avec les poids "
        "officiels OpenAI, puis fine-tuné pour la classification spam/ham. "
        "Accuracy sur le test set : 96.96%. "
        "[Code source](https://github.com/Yatoute/gpt-light)"
    ),
)

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=int(os.environ.get("PORT", "7860")))
