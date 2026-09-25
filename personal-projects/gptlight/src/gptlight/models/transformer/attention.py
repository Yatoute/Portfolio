from __future__ import annotations

import torch
import torch.nn as nn

class SelfAttention(nn.Module):
    
    def __init__(self, d_in:int, d_out:int, qkv_bias:bool=False):
        super().__init__()
        self.W_query = nn.Linear(d_in, d_out, bias=qkv_bias)
        self.W_key = nn.Linear(d_in, d_out, bias=qkv_bias)
        self.W_value = nn.Linear(d_in, d_out, bias=qkv_bias)
        
    def forward(self, x:torch.Tensor):
        keys = self.W_key(x)
        queries = self.W_key(x)
        values = self.W_value(x)
        attn_scores = queries@keys.T
        attn_weights = torch.softmax(attn_scores/keys.shape[-1]**0.5, dim=-1)
        context_vecs = attn_weights@values
        return context_vecs

class CausalAttention(nn.Module):
    
    def __init__(self, d_in:int, d_out:int, context_length:int, dropout:float, qkv_bias:bool=False):
        super().__init__()
        self.W_query = nn.Linear(d_in, d_out, bias=qkv_bias)
        self.W_key = nn.Linear(d_in, d_out, bias=qkv_bias)
        self.W_value = nn.Linear(d_in, d_out, bias=qkv_bias)
        self.dropout = nn.Dropout(dropout)
        self.register_buffer(
            'mask',
            torch.triu(torch.ones(context_length, context_length), diagonal=1)
        )
    
    def forward(self, x:torch.Tensor):
        num_tokens = x.shape[1]
        queries = self.W_query(x)
        keys = self.W_key(x)
        values = self.W_value(x)
        attn_scores = queries@keys.transpose(1, 2)
        attn_scores.masked_fill_(
            self.mask.bool()[:num_tokens, :num_tokens], -torch.inf
        )
        attn_weights = torch.softmax(attn_scores/keys.shape[-1]**0.5, dim=-1)
        attn_weights = self.dropout(attn_weights)
        context_vecs = attn_weights@values
        return context_vecs
        
class MultiHeadAttentionWrapper(nn.Module):
    
    def __init__(self, d_in:int, d_out:int, num_heads:int, context_length:int, dropout:int, qkv_bias:bool=False):
        super().__init__()
        
        self.heads = nn.ModuleList(
            [
                CausalAttention(d_in, d_out, context_length, dropout, qkv_bias) for _ in range(num_heads)
            ]
        )
        
    def forward(self, x:torch.Tensor):
        
        return torch.cat([head(x) for head in self.heads], dim=-1)

class MultiHeadAttention(nn.Module):
    
    def __init__(self, d_in:int, d_out:int, num_heads:int, context_length:int, dropout:float, qkv_bias:bool=False):
        super().__init__()
        assert (d_out % num_heads == 0), "d_out must be divisible by num_heads"
        
        self.d_out = d_out
        self.num_heads = num_heads
        self.head_dim = d_out//num_heads
        self.W_query = nn.Linear(d_in, d_out, bias=qkv_bias)
        self.W_key = nn.Linear(d_in, d_out, bias=qkv_bias)
        self.W_value = nn.Linear(d_in, d_out, bias=qkv_bias)
        self.register_buffer(
            "mask",
            torch.triu(torch.ones(context_length, context_length), diagonal=1)
        )
        self.dropout = nn.Dropout(dropout)
        self.out_proj = nn.Linear(d_out, d_out)
   
    def forward(self, x:torch.Tensor):
        
        num_batchs, num_tokens, d_in = x.shape
        
        keys = self.W_key(x) # -> (num_batchs, num_tokens, d_out=num_heads*head_dim)
        queries = self.W_query(x) # -> (num_batchs, num_tokens, d_out=num_heads*head_dim)
        values = self.W_value(x) # -> (num_batchs, num_tokens, d_out=num_heads*head_dim)
        
        keys = keys.view(num_batchs, num_tokens, self.num_heads, self.head_dim) # -> (num_batchs, num_tokens, num_heads, head_dim)
        queries = queries.view(num_batchs, num_tokens, self.num_heads, self.head_dim)
        values= values.view(num_batchs, num_tokens, self.num_heads, self.head_dim)
        
        keys = keys.transpose(1, 2) # -> (num_batchs, num_heads, num_tokens, head_dim)
        queries = queries.transpose(1, 2)
        values = values.transpose(1, 2)
        
        attn_scores = queries@keys.transpose(-2, -1) # -> (num_batchs, num_heads, num_tokens, num_tokens)
        attn_scores.masked_fill_(
            self.mask.bool()[:num_tokens, :num_tokens], -torch.inf
        )
        
        attn_weights = torch.softmax(attn_scores/keys.shape[-1]**0.5, dim=-1)
        attn_weights = self.dropout(attn_weights)
        
        context_vecs = (attn_weights@values).transpose(1, 2) # -> (num_batchs,  num_tokens, num_heads, head_dim)
        context_vecs = context_vecs.contiguous().view(num_batchs,  num_tokens, self.d_out) # -> (num_batchs,  num_tokens, d_out = num_heads*head_dim)
        
        context_vecs = self.out_proj(context_vecs)
        
        return context_vecs
    
class MHAPyTorchScaledDotProduct(nn.Module):
    def __init__(self, d_in:int, d_out:int, num_heads:int, context_length:int, dropout:float=0.0, qkv_bias:bool=False):
        super().__init__()

        assert d_out % num_heads == 0, "d_out is indivisible by num_heads"

        self.num_heads = num_heads
        self.context_length = context_length
        self.head_dim = d_out // num_heads
        self.d_out = d_out

        self.qkv = nn.Linear(d_in, 3 * d_out, bias=qkv_bias)
        self.proj = nn.Linear(d_out, d_out)
        self.dropout = dropout

    def forward(self, x:torch.Tensor):
        
        num_batchs, num_tokens, embed_dim = x.shape
        
        qkv = self.qkv(x)
        
        qkv = qkv.view(num_batchs, num_tokens, 3, self.num_heads, self.head_dim)
        qkv = qkv.permute(2, 0, 3, 1, 4)
        queries, keys, values = qkv
        
        use_dropout = 0. if not self.training else self.dropout
        
        context_vec = nn.functional.scaled_dot_product_attention(
            queries, keys, values, attn_mask=None, dropout_p=use_dropout, is_causal=True)
        context_vec = context_vec.transpose(1, 2).contiguous().view(num_batchs, num_tokens, self.d_out)
        
        context_vec = self.proj(context_vec)

        return context_vec