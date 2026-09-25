# Conversational query rewriting for scoped semantic retrieval

Le plus important, c’est de **ne pas mélanger** :

* génération du dataset,
* fine-tuning,
* évaluation retrieval,
* code de l’agent ChatPDF.

---

# Structure recommandée

```text
chatpdf-query-rewriter/
│
├── README.md
├── pyproject.toml
├── .env
├── .gitignore
├── configs/
│   ├── generation/
│   │   ├── generation.yaml
│   │   ├── templates.yaml
│   │   └── validation.yaml
│   ├── training/
│   │   ├── sft_lora_qwen.yaml
│   │   ├── sft_lora_mistral.yaml
│   │   └── dpo_or_rl.yaml
│   ├── evaluation/
│   │   ├── retrieval_eval.yaml
│   │   ├── rewrite_eval.yaml
│   │   └── chatpdf_eval.yaml
│   └── serving/
│       └── query_rewriter.yaml
│
├── data/
│   ├── raw/
│   │   ├── documents/
│   │   ├── pdf_text/
│   │   └── metadata/
│   ├── interim/
│   │   ├── conversation_generation/
│   │   ├── extracted_examples/
│   │   ├── validated_examples/
│   │   └── deduplicated/
│   ├── processed/
│   │   ├── rewrite_dataset.jsonl
│   │   ├── rewrite_dataset_sft.jsonl
│   │   ├── train.jsonl
│   │   ├── valid.jsonl
│   │   └── test.jsonl
│   └── evaluation/
│       ├── retrieval_benchmark.jsonl
│       ├── gold_chunks.jsonl
│       └── judged_examples.jsonl
│
├── notebooks/
│   ├── 01_document_exploration.ipynb
│   ├── 02_dataset_audit.ipynb
│   ├── 03_rewrite_analysis.ipynb
│   └── 04_retrieval_eval_analysis.ipynb
│
├── src/
│   ├── __init__.py
│   │
│   ├── common/
│   │   ├── io.py
│   │   ├── logging.py
│   │   ├── hashing.py
│   │   ├── text.py
│   │   ├── json_utils.py
│   │   └── config.py
│   │
│   ├── document_pipeline/
│   │   ├── loader.py
│   │   ├── pdf_parser.py
│   │   ├── markdown_parser.py
│   │   ├── metadata_extractor.py
│   │   └── chunking.py
│   │
│   ├── dataset_generation/
│   │   ├── schemas.py
│   │   ├── prompt_builder.py
│   │   ├── llm_generator.py
│   │   ├── template_generator.py
│   │   ├── extractor.py
│   │   ├── validator.py
│   │   ├── filters.py
│   │   ├── dedup.py
│   │   ├── balancing.py
│   │   └── build_dataset.py
│   │
│   ├── retriever/
│   │   ├── embedder.py
│   │   ├── vector_index.py
│   │   ├── bm25_index.py
│   │   ├── hybrid_search.py
│   │   └── retrieval_metrics.py
│   │
│   ├── training/
│   │   ├── sft_dataset.py
│   │   ├── tokenizer_utils.py
│   │   ├── lora_trainer.py
│   │   ├── reward_trainer.py
│   │   ├── losses.py
│   │   └── export_model.py
│   │
│   ├── evaluation/
│   │   ├── rewrite_metrics.py
│   │   ├── retrieval_eval.py
│   │   ├── end2end_eval.py
│   │   ├── human_eval_sampler.py
│   │   └── error_analysis.py
│   │
│   ├── serving/
│   │   ├── rewrite_service.py
│   │   ├── inference.py
│   │   ├── normalization.py
│   │   └── api.py
│   │
│   └── chatpdf/
│       ├── query_rewriter_node.py
│       ├── retriever_node.py
│       ├── answer_node.py
│       └── pipeline.py
│
├── scripts/
│   ├── prepare_documents.py
│   ├── generate_conversations.py
│   ├── build_rewrite_dataset.py
│   ├── split_dataset.py
│   ├── train_sft.py
│   ├── eval_rewrite.py
│   ├── eval_retrieval.py
│   ├── eval_end2end.py
│   └── run_local_api.py
│
├── tests/
│   ├── test_filters.py
│   ├── test_dedup.py
│   ├── test_template_generation.py
│   ├── test_sft_format.py
│   ├── test_rewrite_service.py
│   └── test_retrieval_eval.py
│
├── artifacts/
│   ├── models/
│   ├── adapters/
│   ├── logs/
│   ├── reports/
│   └── predictions/
│
└── experiments/
    ├── exp_001_baseline_no_rewriter/
    ├── exp_002_prompt_rewriter/
    ├── exp_003_sft_rewriter/
    └── exp_004_retrieval_aware_rewriter/
```

---

# Pourquoi cette structure est bonne

## 1. `data/`

Tu sépares clairement :

* les documents bruts,
* les sorties intermédiaires,
* le dataset final,
* les jeux d’évaluation.

Ça évite le chaos très vite.

---

## 2. `src/dataset_generation/`

C’est le cœur de la génération.

Tu y mets :

* génération LLM,
* génération template,
* extraction,
* validation,
* filtrage,
* équilibrage,
* déduplication.

Il faut vraiment regrouper tout ça au même endroit.

---

## 3. `src/training/`

Tu dois isoler la partie fine-tuning.

Ici tu mets :

* préparation du dataset SFT,
* tokenizer,
* LoRA trainer,
* export du modèle.

Comme ça, ton code de génération de dataset n’est pas mélangé avec le code Hugging Face.

---

## 4. `src/evaluation/`

Très important.

Tu dois évaluer à trois niveaux :

* qualité de la reformulation,
* impact sur le retrieval,
* impact end-to-end sur ChatPDF.

Donc il faut un vrai module dédié.

---

## 5. `src/chatpdf/`

Cette partie contient seulement l’intégration produit :

* node query rewriting,
* node retrieval,
* node answer,
* pipeline global.

Le modèle fine-tuné doit être consommé ici, pas entraîné ici.

---

# Structure minimale si tu veux commencer plus simple

Si tu veux une version plus légère au début :

```text
chatpdf-query-rewriter/
│
├── data/
│   ├── raw/
│   ├── interim/
│   └── processed/
│
├── src/
│   ├── generation/
│   │   ├── generator.py
│   │   ├── extractor.py
│   │   ├── validator.py
│   │   └── build_dataset.py
│   ├── training/
│   │   ├── train.py
│   │   └── dataset.py
│   ├── evaluation/
│   │   ├── eval_rewrite.py
│   │   ├── eval_retrieval.py
│   │   └── eval_end2end.py
│   └── serving/
│       └── rewriter.py
│
├── scripts/
│   ├── generate_dataset.py
│   ├── train.py
│   └── evaluate.py
│
└── experiments/
```

Ça suffit pour un PoC.

---

# Structure logique du workflow

Je te conseille de penser ton projet comme ça :

## A. Préparation documentaire

```text
documents → texte extrait → métadonnées → chunks
```

## B. Construction dataset

```text
documents → conversations synthétiques → exemples de rewrite → validation → dataset final
```

## C. Entraînement

```text
dataset final → SFT / LoRA → adapter ou modèle final
```

## D. Évaluation

```text
baseline sans rewrite
vs
rewrite par prompt
vs
rewrite par modèle fine-tuné
```

## E. Intégration produit

```text
user query → rewriter → retriever → answer
```

---

# Les fichiers les plus importants à avoir dès le début

Je te recommande de créer d’abord :

```text
src/dataset_generation/build_dataset.py
src/dataset_generation/template_generator.py
src/dataset_generation/validator.py
src/training/train.py
src/evaluation/eval_retrieval.py
src/serving/rewrite_service.py
src/chatpdf/query_rewriter_node.py
```

Avec ça, tu as déjà l’ossature utile.

---

# Organisation recommandée des configs

Utilise des YAML séparés.

## `configs/generation/generation.yaml`

Contient :

* nombre de conversations par doc,
* proportion template / LLM,
* températures,
* seuils de filtrage,
* ratio no-change.

## `configs/training/sft_lora_qwen.yaml`

Contient :

* modèle de base,
* max_seq_len,
* batch_size,
* learning_rate,
* LoRA rank,
* epochs.

## `configs/evaluation/retrieval_eval.yaml`

Contient :

* embedding model,
* top_k,
* métriques,
* dataset d’évaluation.

---

# Je te recommande aussi cette convention de nommage

## Dataset

* `rewrite_dataset_v1.jsonl`
* `rewrite_dataset_v2.jsonl`

## Modèles

* `qwen2.5-7b-rewriter-sft-v1`
* `qwen2.5-7b-rewriter-sft-v2`
* `qwen2.5-7b-rewriter-retrievalaware-v1`

## Expériences

* `exp_001_baseline`
* `exp_002_prompt_rewrite`
* `exp_003_sft_lora`
* `exp_004_dual_query`

Ça t’évitera beaucoup de confusion.

---

# Mon conseil d’architecture global

Pour ton projet, je recommande **3 repos séparés** seulement si le projet grossit beaucoup :

1. un repo `dataset-generation`
2. un repo `rewriter-training`
3. un repo `chatpdf-agent`

Mais au début, **un seul repo bien structuré** est meilleur.

---

# Mon organisation idéale pour toi

Vu ton besoin actuel, je te conseille de démarrer avec ces dossiers :

```text
data/
src/document_pipeline/
src/dataset_generation/
src/training/
src/evaluation/
src/chatpdf/
scripts/
experiments/
artifacts/
```

C’est le meilleur compromis entre :

* clarté,
* évolutivité,
* simplicité.

---

Je peux maintenant te proposer directement une **arborescence initiale prête à copier-coller**, avec les fichiers Python vides et un `pyproject.toml` de départ.

