from __future__ import annotations

from typing import Literal, Collection, AbstractSet

import tiktoken
import torch

from .simple_tokenizer import SimpleTokenizer

__all__ = [
    "SimpleTokenizer",
    "GPTTokenizer"
]

class GPTTokenizer():
    
    def __init__(self, encoding_name:str="gpt2"):
        
        self.encoding_name = encoding_name
        self.encoding = tiktoken.get_encoding(encoding_name)
            
    def encode(self, 
            text:str, 
            allowed_special: AbstractSet[str] | Literal['all'] = {'<|endoftext|>'}, 
            disallowed_special: Collection[str] | Literal['all'] = "all"
        ) -> torch.Tensor:
        """Encodes a string into tokens"""
        
        encoded = self.encoding.encode(text=text, allowed_special=allowed_special, disallowed_special=disallowed_special)
        return torch.tensor(encoded)
        
    def decode(self, 
        tokens:torch.Tensor,
        errors: str = "replace"
    ) -> str:
        """Decodes a tensor of tokens into a string."""
        
        flat = tokens.squeeze(0)
        return self.encoding.decode(flat.tolist(), errors)