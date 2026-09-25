from __future__ import annotations

from torch.utils.data import Dataset

from gptlight.tokenizer import GPTTokenizer

class PreTrainingDataset(Dataset):
    """ Pre Training Dataset"""
    
    def __init__(self, txt:str, tokenizer:GPTTokenizer, max_length:int, stride:int):
        
        self.input_ids = []
        self.target_ids = []
        
        token_ids = tokenizer.encode(txt)
        
        for i in range(0, len(token_ids)-max_length, stride):
            input_chunk = token_ids[i:i+max_length]
            target_chunk = token_ids[i+1:i+max_length+1]
            self.input_ids.append(input_chunk)
            self.target_ids.append(target_chunk)
    
    def __len__(self):
        return len(self.input_ids)
    
    def __getitem__(self, idx):
        return self.input_ids[idx], self.target_ids[idx]
