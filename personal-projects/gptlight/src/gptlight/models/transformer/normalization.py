from __future__ import annotations
import torch
import torch.nn as nn

class LayerNorm(nn.Module):
    
    def __init__(self, emb_dim:int):
        super().__init__()
        self.eps = 1e-5
        self.scale = nn.Parameter(torch.ones(emb_dim))
        self.shift = nn.Parameter(torch.zeros(emb_dim))
    
    def forward(self, x:torch.Tensor):
        
        x_mean = x.mean(dim=-1, keepdim=True)
        x_var = x.var(dim=-1, keepdim=True, unbiased=False)
        x_norm = (x-x_mean)/torch.sqrt(x_var + self.eps)
        
        return self.scale*x_norm + self.shift