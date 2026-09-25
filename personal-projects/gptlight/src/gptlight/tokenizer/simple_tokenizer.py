from __future__ import annotations
from typing import Dict, List

import re

class SimpleTokenizer:
    """
        A first single tokenizer 
    """
    def __init__(self, vocab:Dict[int]):
        self.str_to_int = vocab
        self.int_to_str = {token_id:token for token, token_id in vocab.items()}
    
    def encode(self, text:str) -> List[int]:
        """
            Map the input text to tokens id
        """
        preprocessed = re.split(r'([,.:;?_!"()\']|--|\s)', text)
           
        return [self.str_to_int.get(token.strip()) or self.str_to_int.get("<|unk|>") for token in preprocessed if token.strip()]
    
    def decode(self, ids:List[int]) -> List[str]:
        """
            Converts token IDs back into text
        """
        
        text = " ".join([self.int_to_str[id] for id in ids])
        
        return re.sub(r'\s+([,.?!"()\'])', r'\1', text) # Remove space before the specified ponctuation