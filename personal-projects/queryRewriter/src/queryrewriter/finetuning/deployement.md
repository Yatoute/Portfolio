# Déploiement final sur Runpod — Fine-tuning de `Qwen/Qwen3.5-35B-A3B` avec LLaMA-Factory

Ce guide te permet de partir de ton projet local et d’aller jusqu’au lancement du fine-tuning sur Runpod, avec une configuration simple, persistante et adaptée à ton projet.

---

## 1. Objectif

À la fin de ce guide, tu auras :

- un **Network Volume** Runpod pour conserver dataset, checkpoints et logs ;
- un **Pod GPU** attaché à ce volume ;
- ton projet cloné dans `/workspace` ;
- LLaMA-Factory installé ;
- les jeux `train.json` et `test.json` générés ;
- le fine-tuning lancé sur `Qwen/Qwen3.5-35B-A3B` en **QLoRA**.

---

## 2. Configuration Runpod recommandée

Utilise la configuration suivante :

- **Workload** : `Pod GPU`
- **Cloud** : `Secure Cloud`
- **GPU** : `1x A100 80GB`
- **Template** : un template PyTorch officiel récent disponible dans Runpod
- **Container Disk** : `40 à 60 GB`
- **Network Volume** : `100 à 200 GB`
- **Mount point** : `/workspace`
- **Exposed services** : `SSH` et éventuellement `Jupyter/HTTP`
- **Nom du Pod** : par exemple `qwen35b-rewriter-train`

### Pourquoi cette configuration

- Le **Network Volume** permet de conserver les données même après terminaison du Pod.
- Le **Container Disk** sert au temporaire.
- Pour un fine-tuning LLM, un **A100 80GB** est un choix solide.
- Le template PyTorch officiel réduit les problèmes d’environnement.

---

## 3. Préparer ton projet local

Avant d’envoyer ton projet sur Runpod, fais ces corrections dans ton repo local.


### 3.1 Structure finale attendue

Ton projet doit ressembler à ceci :

```text
.
├── configs
│   └── llamafactory
│       └── qwen3_rewriter_qlora.yaml
├── dataset
│   ├── dataset_info.json
│   └── dataset_sft.jsonl
├── outputs
├── requirements.txt
├── scripts
│   ├── evaluate.sh
│   ├── split_data.sh
│   └── train.sh
└── src
    ├── config.py
    ├── evaluate.py
    ├── __init__.py
    ├── metrics.py
    ├── prompts.py
    ├── training.py
    ├── train_test_split.py
    └── utils.py
```

### 3.3 Corriger le YAML

Dans `configs/llamafactory/qwen3_rewriter_qlora.yaml`, utilise ceci :

```yaml
model_name_or_path: Qwen/Qwen3.5-35B-A3B
stage: sft
do_train: true
finetuning_type: lora
quantization_bit: 4
template: qwen

dataset_dir: dataset
dataset: qwen_rewriter_train
eval_dataset: qwen_rewriter_test

cutoff_len: 1024
max_samples: 1000000
overwrite_cache: true
preprocessing_num_workers: 8

output_dir: saves/qwen3_35b_a3b_rewriter_qlora
logging_steps: 10
save_steps: 200
eval_steps: 200
plot_loss: true
overwrite_output_dir: true
save_total_limit: 2

per_device_train_batch_size: 1
per_device_eval_batch_size: 1
gradient_accumulation_steps: 16
learning_rate: 2.0e-5
num_train_epochs: 2.0
lr_scheduler_type: cosine
warmup_ratio: 0.03
bf16: true
ddp_timeout: 180000000
flash_attn: fa2

lora_rank: 16
lora_alpha: 32
lora_dropout: 0.05
lora_target: all

val_size: 0.0
evaluation_strategy: steps
save_strategy: steps

report_to: none
```

### 3.4 Vérifier `dataset_info.json`

Le fichier `dataset/dataset_info.json` doit être :

```json
{
  "qwen_rewriter_train": {
    "file_name": "train.json",
    "formatting": "sharegpt",
    "columns": {
      "messages": "messages"
    }
  },
  "qwen_rewriter_test": {
    "file_name": "test.json",
    "formatting": "sharegpt",
    "columns": {
      "messages": "messages"
    }
  }
}
```

### 3.5 `scripts/split_data.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

python -m src.train_test_split \
  --input dataset/dataset_sft.jsonl \
  --output-dir dataset \
  --test-size 0.1 \
  --seed 42
```

### 3.6  `scripts/train.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

python -m src.training \
  --config configs/llamafactory/qwen3_rewriter_qlora.yaml \
  --workdir /workspace/LLaMA-Factory
```

### 3.7 `scripts/evaluate.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

python -m src.evaluate \
  --references dataset/test.json \
  --predictions outputs/predictions.jsonl
```

---

## 4. Mettre ton projet sur Git

Le plus simple sur Runpod est de **cloner ton projet depuis Git**.

### 4.1 Initialiser Git si ce n’est pas déjà fait

```bash
git init
git add .
git commit -m "Initial training project"
```

### 4.2 Pousser vers GitHub, GitLab ou autre

Exemple :

```bash
git remote add origin <URL_DE_TON_REPO>
git branch -M main
git push -u origin main
```

---

## 5. Créer le Network Volume sur Runpod

Dans l’interface Runpod :

1. Va dans **Storage**.
2. Crée un **Network Volume**.
3. Choisis une région/datacenter où un **A100 80GB** est disponible.
4. Mets une taille de **200 GB**.
5. Donne-lui un nom clair, par exemple `qwen-rewriter-volume`.

### Important

Le volume doit être créé dans une région compatible avec le GPU que tu veux louer. Le Pod sera contraint par la localisation du volume.

---

## 6. Déployer le Pod GPU

Dans Runpod :

1. Va dans **Pods**.
2. Clique sur **Deploy**.
3. Choisis **Secure Cloud**.
4. Sélectionne **1x A100 80GB**.
5. Attache le **Network Volume** créé à l’étape précédente.
6. Choisis un template **PyTorch officiel récent**.
7. Mets :
   - **Container Disk** : `40–60 GB`
   - **Mount point** : `/workspace`
   - **SSH** activé
   - **Jupyter/HTTP** activé si tu veux
8. Donne un nom au Pod.
9. Clique sur **Deploy On-Demand**.

---

## 7. Se connecter au Pod

Quand le Pod est prêt, tu peux utiliser :

- le terminal web Runpod ;
- SSH ;
- éventuellement Jupyter.

Pour démarrer rapidement, le terminal web Runpod suffit.

---

## 8. Préparer l’environnement dans le Pod

Dans le terminal du Pod, exécute les commandes suivantes.

### 8.1 Aller dans `/workspace`

```bash
cd /workspace
```

### 8.2 Cloner LLaMA-Factory

```bash
git clone https://github.com/hiyouga/LLaMA-Factory.git
```

### 8.3 Cloner ton projet

```bash
git clone <URL_DE_TON_REPO> qwen_rewriter_ft
```

### 8.4 Vérifier l’arborescence

```bash
ls -la /workspace
ls -la /workspace/qwen_rewriter_ft
```

---

## 9. Installer les dépendances

### 9.1 Mettre à jour pip

```bash
python -m pip install --upgrade pip setuptools wheel
```

### 9.2 Installer LLaMA-Factory

```bash
cd /workspace/LLaMA-Factory
pip install -e ".[torch,metrics]"
```

### 9.3 Installer les dépendances de ton projet

```bash
cd /workspace/qwen_rewriter_ft
pip install -r requirements.txt
```

---

## 10. Vérifier que le GPU fonctionne

```bash
nvidia-smi
python -c "import torch; print(torch.__version__); print(torch.cuda.is_available())"
```

Tu dois voir :

- le GPU A100 dans `nvidia-smi` ;
- `True` pour `torch.cuda.is_available()`.

---

## 11. Générer `train.json` et `test.json`

Depuis ton projet :

```bash
cd /workspace/qwen_rewriter_ft
bash scripts/split_data.sh
```

Puis vérifie :

```bash
ls -lh dataset/
```

Tu dois avoir au minimum :

- `dataset_sft.jsonl`
- `dataset_info.json`
- `train.json`
- `test.json`
- `stats.json`

---

## 12. Lancer le fine-tuning

Toujours depuis ton projet :

```bash
cd /workspace/qwen_rewriter_ft
bash scripts/train.sh
```

Cela appellera ton module `src.training`, qui exécute :

```bash
llamafactory-cli train <chemin_du_yaml>
```

avec `LLaMA-Factory` comme répertoire de travail.

---

## 13. Où seront stockés les fichiers de sortie

Les sorties seront créées dans :

```text
/workspace/qwen_rewriter_ft/saves/qwen3_35b_a3b_rewriter_qlora/
```

Tu y trouveras notamment :

- les adapters LoRA ;
- les checkpoints ;
- les logs ;
- les fichiers de configuration générés.

Comme `/workspace` est monté sur le **Network Volume**, ces données resteront disponibles même si tu termines le Pod.

---

## 14. Suivre l’entraînement

Pendant le fine-tuning, surveille :

- les logs dans le terminal ;
- la création des checkpoints dans `saves/...`.

Commandes utiles :

```bash
ls -lh saves/qwen3_35b_a3b_rewriter_qlora/
```

```bash
du -sh saves/qwen3_35b_a3b_rewriter_qlora/
```

---

## 15. Évaluer le modèle

Quand tu auras un fichier de prédictions au format JSONL, lance :

```bash
cd /workspace/qwen_rewriter_ft
bash scripts/evaluate.sh
```

Le script calcule :

- `exact_match`
- `token_f1`
- `semantic_similarity`

---

## 16. Récupérer les résultats

Tu peux :

- laisser les fichiers sur le **Network Volume** ;
- les télécharger ;
- les pousser vers Hugging Face ;
- rattacher le même volume à un nouveau Pod plus tard.

Les éléments les plus importants à conserver sont :

- le dossier des adapters LoRA ;
- les logs ;
- les métriques ;
- les fichiers de config utilisés.

---

## 17. Arrêter le travail sans perdre les données

### Cas 1 — Pod avec Network Volume

Le Pod ne se stoppe pas au sens classique : tu le **termines**. Les données présentes sur le Network Volume restent conservées.

### Cas 2 — Pod sans Network Volume

Un Pod classique peut être **stoppé**, ce qui garde les données du volume disque `/workspace`, mais efface le container disk.

Dans ton cas, comme tu utilises un **Network Volume**, tu travailleras avec la logique :

- **Terminate Pod** quand tu as fini ;
- **garder le volume** ;
- **recréer un Pod** plus tard si nécessaire.

---

## 18. Reprendre plus tard

Quand tu veux reprendre :

1. recrée un Pod ;
2. rattache le **même Network Volume** ;
3. vérifie que `/workspace` contient toujours :

```text
/workspace/LLaMA-Factory
/workspace/qwen_rewriter_ft
```

4. relance ton travail.

---

## 19. Script bootstrap optionnel

Si tu veux automatiser l’installation et le lancement, crée dans `/workspace` un fichier `bootstrap_runpod.sh` :

```bash
#!/usr/bin/env bash
set -euo pipefail

WORKSPACE=/workspace
PROJECT_DIR=$WORKSPACE/qwen_rewriter_ft
LLAMA_FACTORY_DIR=$WORKSPACE/LLaMA-Factory

echo "=== GPU check ==="
nvidia-smi

echo "=== Python check ==="
python --version
pip --version

echo "=== Upgrade pip tools ==="
python -m pip install --upgrade pip setuptools wheel

echo "=== Clone repositories ==="
cd "$WORKSPACE"

if [ ! -d "$LLAMA_FACTORY_DIR" ]; then
  git clone https://github.com/hiyouga/LLaMA-Factory.git
fi

if [ ! -d "$PROJECT_DIR" ]; then
  git clone <URL_DE_TON_REPO> qwen_rewriter_ft
fi

echo "=== Install LLaMA-Factory ==="
cd "$LLAMA_FACTORY_DIR"
pip install -e ".[torch,metrics]"

echo "=== Install project requirements ==="
cd "$PROJECT_DIR"

if [ -f requirements.txt ]; then
  pip install -r requirements.txt
else
  echo "ERROR: requirements.txt not found"
  exit 1
fi

echo "=== Create output directories ==="
mkdir -p "$PROJECT_DIR/outputs"
mkdir -p "$PROJECT_DIR/saves"

echo "=== Torch CUDA sanity check ==="
python - <<'PY'
import torch
print("torch:", torch.__version__)
print("cuda available:", torch.cuda.is_available())
if torch.cuda.is_available():
    print("device:", torch.cuda.get_device_name(0))
PY

echo "=== Split dataset ==="
cd "$PROJECT_DIR"
bash scripts/split_data.sh

echo "=== Check split outputs ==="
ls -lh dataset/train.json dataset/test.json dataset/stats.json

echo "=== Launch training ==="
bash scripts/train.sh
```

Puis :

```bash
cd /workspace
chmod +x bootstrap_runpod.sh
./bootstrap_runpod.sh
```

---

## 20. Checklist rapide avant de lancer

Avant le training, vérifie ceci :

- [ ] `requirements.txt` existe
- [ ] `dataset_dir: dataset` dans le YAML
- [ ] `dataset/dataset_info.json` est correct
- [ ] `dataset/dataset_sft.jsonl` est présent
- [ ] `scripts/split_data.sh` utilise `python -m src.train_test_split`
- [ ] `scripts/train.sh` utilise `python -m src.training`
- [ ] `train.json` et `test.json` ont été générés
- [ ] `nvidia-smi` fonctionne
- [ ] `torch.cuda.is_available()` renvoie `True`

---

## 21. Séquence minimale de commandes

Si tout est prêt, voici la séquence minimale à exécuter dans le Pod :

```bash
cd /workspace
git clone https://github.com/hiyouga/LLaMA-Factory.git
git clone <URL_DE_TON_REPO> qwen_rewriter_ft

cd /workspace/LLaMA-Factory
pip install -e ".[torch,metrics]"

cd /workspace/qwen_rewriter_ft
pip install -r requirements.txt
bash scripts/split_data.sh
bash scripts/train.sh
```

---

## 22. Résultat attendu

À la fin, tu auras :

- ton dataset préparé ;
- le fine-tuning lancé ;
- les artefacts stockés dans `/workspace/qwen_rewriter_ft/saves/...` ;
- des données persistantes grâce au **Network Volume**.

---

## 23. Conseils pratiques

- Ne travaille pas sur le container disk pour les fichiers importants.
- Garde tout ce qui compte dans `/workspace`.
- Commence avec un petit sous-ensemble du dataset pour valider le pipeline.
- Vérifie toujours que `train.json` et `test.json` sont bien créés avant le training.
- Si le Pod disparaît, tes données restent sur le Network Volume tant que tu ne supprimes pas ce volume.

---

## 24. Ce que tu feras ensuite

Une fois le fine-tuning terminé, les prochaines étapes naturelles seront :

1. tester l’adapter LoRA ;
2. mesurer les performances sur ton jeu de test ;
3. éventuellement merger ou publier l’adapter ;
4. déployer une inference avec vLLM ou un autre serveur.

