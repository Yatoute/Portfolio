from __future__ import annotations

from typing import Iterable, Optional
import torch
from torch.utils.data import Dataset

from gptlight.tokenizer import GPTTokenizer

class ClassifcationDataset(Dataset):
    
    def __init__(
        self, 
        input_texts:Iterable[str],
        target_labels:Iterable[int],
        tokenizer:GPTTokenizer, 
        max_lenght:Optional[int]=None,
        pad_token_id:int=50256
    )-> None:
        
        self.input_texts = list(input_texts)
        self.target_labels = list(target_labels)
        
        self.encoded_texts = [
            tokenizer.encode(text).tolist() for text in self.input_texts
        ]
        if max_lenght is None:
            self.max_length = self._longest_encoded_length()
        else:
            self.max_length = max_lenght
            
            self.encoded_texts = [
                encoded_text[:self.max_length] for encoded_text in self.encoded_texts
            ]
        
        self.encoded_texts = [
            encoded_text + [pad_token_id]*(self.max_length - len(encoded_text))
            for encoded_text in self.encoded_texts
        ]
    
    def __getitem__(self, index):
        encoded = self.encoded_texts[index]
        label = self.target_labels[index]
        return (
            torch.tensor(encoded, dtype=torch.long),
            torch.tensor(label, dtype=torch.long)
        )
    
    def __len__(self):
        return len(self.input_texts)
        
    def _longest_encoded_length(self) -> int:
        max_length = 0
        for encoded_text in self.encoded_texts:
            encoded_lenght = len(encoded_text)
            if encoded_lenght > max_length:
                max_length = encoded_lenght
        
        return max_length
        