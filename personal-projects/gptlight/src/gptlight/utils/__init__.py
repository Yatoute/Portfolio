from __future__ import annotations

import torch

from .data import fetch_verdict_text, fetch_sms_spam_collection
from .gpt_download import download_and_load_gpt2, load_gpt2_from_local

__all__ = [
    "fetch_verdict_text",
    "fetch_sms_spam_collection",
    "fetch_instructions_data"
    "download_and_load_gpt2",
    "load_gpt2_from_local",
    "generate_text_simple"
]

def generate_text_simple(model, idx, max_new_tokens, context_size):
    
    for _ in range(max_new_tokens):
        idx_cond = idx[:, -context_size:]
        with torch.no_grad():
            logits = model(idx_cond)
        logits = logits[:, -1, :]
        probas = torch.softmax(logits, dim=-1)
        idx_next = torch.argmax(probas, dim=-1, keepdim=True)
        idx = torch.cat((idx, idx_next), dim=1)
        
    return idx