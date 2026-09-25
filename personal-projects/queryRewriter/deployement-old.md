## Config RunPod exacte que je recommande

Pour le **Plan A QLoRA** sur **Qwen3.5-35B-A3B**, prends :

* **Type de workload** : Pod GPU
* **Cloud** : **Secure Cloud**
* **GPU** : **1× A100 80GB**
* **Template / image** : `runpod/pytorch:2.2.0-py3.10-cuda12.1.1-devel-ubuntu22.04`
* **Container Disk** : **40 à 60 GB**
* **Network Volume** : **150 à 250 GB**
* **Mount point** : `/workspace`
* **Expose** : SSH + Jupyter/HTTP si tu veux travailler dans le navigateur
* **Pod name** : par exemple `qwen35b-rewriter-train`

Pourquoi cette config :

* Runpod propose bien des GPU type **A100** et **H100** dans son offre cloud GPU. ([Runpod][2])
* Le template PyTorch officiel Runpod avec **PyTorch 2.2 + CUDA 12.1** est prévu pour des workloads d’entraînement modernes et évite une bonne partie des problèmes d’environnement. ([Runpod][3])
* Le **Network Volume** est le bon choix pour stocker dataset, checkpoints, logs et adapters, car il persiste indépendamment du Pod et peut être réattaché plus tard. ([Runpod][4])

## Pourquoi je recommande Network Volume ici

Pour un fine-tuning, tu veux éviter de perdre :

* ton dataset nettoyé,
* tes splits train/test,
* tes checkpoints LoRA,
* et tes logs.

Runpod indique que les **Network Volumes remplacent le volume disque par défaut du Pod**, généralement monté sur `/workspace`, et que les données y restent même si tu termines le Pod. En revanche, ils doivent être attachés au moment du déploiement, pas après. ([Runpod Documentation][1])

Point important : un Pod avec **Network Volume** ne se “stop” pas comme un Pod classique ; il se **terminate**, mais les données du volume persistent. Les Pods sans network volume peuvent être stoppés, ce qui libère le GPU mais garde les données du volume disque. ([Runpod Documentation][5])

## Déploiement sur RunPod, étape par étape

### 1. Créer le volume

Dans la console Runpod, crée d’abord un **Network Volume** dans la région où tu comptes prendre ton GPU. Comme l’attachement d’un volume contraint le déploiement au datacenter du volume, mieux vaut créer le volume là où tu vois effectivement des A100 disponibles. Runpod précise aussi qu’avec un seul volume, la dispo GPU dépend du datacenter du volume. ([Runpod Documentation][1])

### 2. Déployer le Pod

Dans **Pods > Deploy** :

* choisis **Network Volume**
* sélectionne ton volume
* choisis **A100 80GB**
* mets comme image : `runpod/pytorch:2.2.0-py3.10-cuda12.1.1-devel-ubuntu22.04`
* mets un nom
* clique **Deploy On-Demand**

Runpod documente précisément ce flow : Pods page → Deploy → éventuellement volume réseau → choix GPU → template/image → Deploy On-Demand. ([Runpod Documentation][5])

## Comment déployer ton programme sur RunPod

Le plus simple est de considérer RunPod comme une machine distante GPU avec `/workspace` persistant.

### Option recommandée : Git clone + installation sur le Pod

Une fois le Pod lancé, ouvre le terminal web ou connecte-toi en SSH, puis :

```bash
cd /workspace
git clone https://github.com/hiyouga/LLaMA-Factory.git
git clone <TON_REPO_GIT> qwen_rewriter_ft

cd /workspace/LLaMA-Factory
pip install -e ".[torch,metrics]"

cd /workspace/qwen_rewriter_ft
pip install -r requirements.txt
```

Ensuite tu lances ton split puis le training :

```bash
cd /workspace/qwen_rewriter_ft
bash scripts/split_data.sh
python src/training.py --config configs/llamafactory/qwen3_rewriter_qlora.yaml --workdir /workspace/LLaMA-Factory
```

### Option 2 : upload de code sur le volume

Tu peux aussi uploader ton projet directement sur le **Network Volume** avant de lancer le Pod, car Runpod expose un **S3-compatible API** pour gérer les fichiers des network volumes sans lancer de compute. ([Runpod Documentation][1])

Mais en pratique, pour itérer vite, `git clone` est plus simple.

## Où mettre les fichiers

Je te conseille cette structure sur le Pod :

```text
/workspace/
├── LLaMA-Factory/
├── qwen_rewriter_ft/
├── datasets/
└── outputs/
```

Et dans ton projet :

* `data/raw/` pour le dataset nettoyé
* `data/processed/` pour train/test
* `saves/` ou `outputs/` pour les adapters LoRA et logs

Comme `/workspace` est sur le volume persistant, tout ça survivra à la fin du Pod. ([Runpod Documentation][1])

## Vérifications juste après le démarrage

Dans le Pod, lance :

```bash
python -c "import torch; print(torch.__version__); print(torch.cuda.is_available())"
nvidia-smi
```

Runpod recommande ce type de vérification simple pour valider que l’environnement PyTorch et l’accès GPU sont bien opérationnels. ([Runpod][3])

## Commande type de lancement

Depuis `/workspace/qwen_rewriter_ft` :

```bash
bash scripts/split_data.sh
python src/training.py \
  --config configs/llamafactory/qwen3_rewriter_qlora.yaml \
  --workdir /workspace/LLaMA-Factory
```

Et pendant l’entraînement :

* checkpoints LoRA dans ton `output_dir`
* logs visibles dans la console du Pod
* fichiers persistés sur `/workspace`

## Comment récupérer le modèle à la fin

Tu auras surtout besoin de :

* dossier des **adapters LoRA**
* config/tokenizer si nécessaire
* logs et éventuellement métriques

Tu peux ensuite :

* les laisser sur le network volume,
* les télécharger,
* ou les pousser vers Hugging Face.

Runpod a aussi une doc de fine-tuning qui inclut la logique générale “fine-tune puis test puis push to Hugging Face”, même si dans ton cas on ne passe pas par leur Axolotl UI. ([Runpod Documentation][6])

## Ma recommandation finale, concrète

Prends :

* **Secure Cloud**
* **1× A100 80GB**
* **image PyTorch officielle Runpod**
* **Network Volume 200 GB**
* déploie ton code par **git clone**
* travaille intégralement dans `/workspace`

C’est la solution la plus simple, la plus robuste, et la plus adaptée à ton pipeline modulaire.

La prochaine étape utile est de te donner un **script `bootstrap_runpod.sh`** qui installe automatiquement LLaMA-Factory, tes dépendances, prépare les dossiers, puis lance le split et le training en une commande.

[1]: https://docs.runpod.io/storage/network-volumes "Network volumes - Runpod Documentation"
[2]: https://www.runpod.io/product/cloud-gpus?utm_source=chatgpt.com "Cloud GPU Instances for AI Workloads"
[3]: https://www.runpod.io/articles/guides/pytorch-2-2-cuda-12-1 "Deploy PyTorch 2.2 with CUDA 12.1 on Runpod for Stable, Scalable AI Workflows"
[4]: https://www.runpod.io/articles/guides/gpu-infrastructure-playbook-for-ai-startups?utm_source=chatgpt.com "The GPU Infrastructure Playbook for AI Startups"
[5]: https://docs.runpod.io/pods/manage-pods "Manage Pods - Runpod Documentation"
[6]: https://docs.runpod.io/fine-tune "Fine-tune a model - Runpod Documentation"
