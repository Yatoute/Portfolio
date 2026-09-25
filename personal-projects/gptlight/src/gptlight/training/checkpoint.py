from __future__ import annotations

from typing import Optional

import torch
import torch.nn as nn

def save_model(
    model:nn.Module, 
    path:str,
    optimizer:Optional[torch.optim.Optimizer]=None,
    epoch:Optional[int]=None
) -> None:
    
    checkpoint = {
        "model_state": model.state_dict()
    }
    
    if isinstance(optimizer, torch.optim.Optimizer):
        checkpoint["optimizer_state"] = optimizer.state_dict()
    
    if isinstance(epoch, int):
        checkpoint["epoch"] = epoch
        
    torch.save(checkpoint, path)

def load_model(path:str, device:torch.device):
    state = torch.load(path, map_location=device)
    
    #model.load_state_dict(state)
    #model.eval()
    return state
