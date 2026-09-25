from __future__ import annotations

import torch
import torch.nn as nn

from .activation import GELU
from gptlight.config import GPTConfig

class FeedForward(nn.Module):
    
    def __init__(self, cfg:GPTConfig):
        super().__init__()
        
        self.layers = nn.Sequential(
            nn.Linear(cfg.emb_dim, 4*cfg.emb_dim),
            GELU(),
            nn.Linear(4*cfg.emb_dim, cfg.emb_dim),
        )
    
    def forward(self, x:torch.Tensor):
        return self.layers(x)