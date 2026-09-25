from __future__ import annotations

from typing import Optional, Callable, Dict, List
import torch
import torch.nn as nn
from torch import Tensor
from torch.utils.data import DataLoader

from .loss import llm_loss, classification_loss
from .checkpoint import save_model, load_model
from .load_pretrained_weights import load_weights_into_gpt

__all__ = [
    "llm_loss",
    "classification_loss",
    "save_model",
    "load_model",
    "load_weights_into_gpt",
    "Trainer",
]

class Trainer:
    """
    A trainer for GPT-like language models (autoregressive LM).
    """
    
    def __init__(
        self,
        model: nn.Module,
        optimizer: torch.optim.Optimizer,
        device: torch.device,
        *,
        loss_fn: Callable[[Tensor, Tensor], Tensor] = llm_loss,
        grad_clip: Optional[float] = None,
        
    ) -> None:
        self.model = model
        self.optimizer = optimizer
        self.device = device
        self.loss_fn = loss_fn
        self.grad_clip = grad_clip
    
    def _forward_batch(
        self,
        input_batch: Tensor,
    )-> Tensor:
        """
        Forward pass helper: moves inputs to device and calls the model.
        """
        input_batch = input_batch.to(self.device)
        logits = self.model(input_batch)
        return logits
    
    def _compute_loss(
        self,
        logits: Tensor,
        targets: Tensor
    )-> Tensor:
        """
        Compute the loss for given logits and targets.
        """
        targets = targets.to(self.device)
        return self.loss_fn(logits, targets)
    
    def train_one_epoch(
        self,
        train_loader: DataLoader
        
    ) -> float:
        """
        Train the model for a single epoch on the given DataLoader.
        Returns the average training loss.
        """
        self.model.train()
        
        total_loss = 0.0
        train_sample_size = len(train_loader)
        
        if train_sample_size ==0:
            return float("nan")
        
        for input_batch, target_batch in train_loader:
            self.optimizer.zero_grad()
            
            logits = self._forward_batch(input_batch)
            loss = self._compute_loss(logits, target_batch)
            
            loss.backward()
            
            if self.grad_clip is not None:
                torch.nn.utils.clip_grad_norm_(
                    self.model.parameters(),
                    self.grad_clip
                )
            self.optimizer.step()
            total_loss += loss.item()
        
        return total_loss/train_sample_size
    
    @torch.no_grad()
    def evaluate(
        self,
        data_loader: DataLoader,
        num_batches: Optional[int]=None
    )-> float:
        """
        Evaluate the model on the given DataLoader.
        Returns the average loss.

        If num_batches is provided, only evaluates on the first num_batches.
        """     
        self.model.eval()
        total_loss = 0.0
        data_sample_size = len(data_loader)
        
        if data_sample_size ==0:
            return float("nan")
        
        if num_batches is None:
            num_batches = data_sample_size
        else: 
            num_batches = min(num_batches, data_sample_size)
        
        for i, (input_batch, target_batch) in enumerate(data_loader):
            
            if i >= num_batches:
                break
            
            logits = self._forward_batch(input_batch)
            loss = self._compute_loss(logits, target_batch)
            total_loss += loss.item()
        
        return total_loss/num_batches
    
    def fit(self,
        train_loader:DataLoader,
        val_loader:Optional[DataLoader]=None,
        num_epochs:int=1,
        *,
        eval_every:int=1,
        verbose:bool=True
    ) -> Dict[str, List[float]]:
        """
        Full training loop over multiple epochs.

        Args:
            train_loader: training DataLoader (input, target)
            val_loader:   optional validation DataLoader
            num_epochs:   number of epochs to train
            eval_every:   evaluate on validation set every N epochs
            verbose:      print progress if True

        Returns:
            history: dict with keys "train_loss" and (optionally) "val_loss"
        """
        history: Dict[str, List[float]] = {
            "train_loss": [],
        }
        
        if val_loader is not None:
            history["val_loss"] = []
        
        for epoch in range(1, num_epochs + 1):
            train_loss = self.train_one_epoch(train_loader)
            history["train_loss"].append(train_loss)
            
            if verbose:
                msg = f"[Epoch {epoch}/{num_epochs}] train_loss={train_loss:.4f}"
            
            if val_loader is not None and(epoch % eval_every ==0):
                val_loss = self.evaluate(val_loader)
                history["val_loss"].append(val_loss)
                
                if verbose:
                    msg += f" | val_loss={val_loss:.4f}"
            
            if verbose:
                print(msg)
        
        return history