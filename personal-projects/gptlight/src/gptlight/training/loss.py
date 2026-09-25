from __future__ import annotations

import torch.nn.functional as F
from torch import Tensor

def llm_loss(logits:Tensor, targets:Tensor):
    """
    Cross-entropy loss for autoregressive language modeling.

    Args:
        logits:  (B, T, V)
        targets: (B, T)

    Returns:
        Scalar tensor (loss)
    """
    batch_size, num_tokens, vocab_size = logits.shape
    
    return F.cross_entropy(
        logits.view(batch_size*num_tokens, vocab_size),
        targets.view(batch_size*num_tokens)
    )


def classification_loss(logits:Tensor, targets:Tensor):
    """
    Cross-entropy loss for classification fine-tuning.

    Args:
        logits:  (B, T, V)
        targets: (B, T)

    Returns:
        Scalar tensor (loss)
    """
    
    return F.cross_entropy(
        logits[:, -1, :],
        targets
    )