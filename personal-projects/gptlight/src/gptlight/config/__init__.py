from dataclasses import dataclass

@dataclass
class GPTConfig:
    vocab_size: int = 50257         # Taille du vocabulaire
    context_length: int = 1024      # Longueur du contexte (n positions max)
    emb_dim: int = 768              # Dimension de l'embedding et du modèle
    n_heads: int = 12               # Nombre de têtes d'attention
    n_layers: int = 12              # Nombre de couches Transformer
    drop_rate: float = 0.1          # Probabilité de dropout
    qkv_bias: bool = False          # Bias sur les matrices Q/K/V

GPT2_CONFIG_124M = GPTConfig()
