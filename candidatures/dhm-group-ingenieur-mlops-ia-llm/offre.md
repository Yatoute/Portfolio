# Ingénieur·e MLOps IA / LLM (H/F) — DHM GROUP

- **Contrat** : non spécifié — mission longue durée
- **Lieu** : 60 Avenue Charles De Gaulle, 92200 Neuilly-sur-Seine — télétravail partiel
- **Rémunération** : non spécifiée
- **Publiée / actualisée** : non spécifiée
- **Candidature préparée le** : 2026-08-22

## Qui sont-ils

DHM Group. Le poste intègre une **IA FACTORY** interne dédiée à la mise en production de modèles d'IA dans un contexte à fort enjeu (contenus, automatisation, recherche d'information).

## Missions

- Industrialiser, fiabiliser et sécuriser la mise en production de modèles IA (LLM, RAG, NLP, Computer Vision…)
- Mettre en place des pipelines robustes et traçables, de l'entraînement à la mise en ligne
- Implémenter une culture MLOps moderne : monitoring, feedback loops, versioning, reproductibilité
- Accompagner les équipes Data Science et DevOps pour faire le lien entre recherche et production

## Profil recherché

**Indispensable** : 3 ans d'expérience minimum en MLOps / déploiement IA-ML en production, très bonne maîtrise de Python (NumPy, Pandas, scikit-learn, PyTorch ou TensorFlow), bon niveau SQL, travail en équipe produit/data/dev/ops en agile, esprit structuré et rigoureux orienté fiabilité long terme.

**Souhaité** : Kubernetes / Kubeflow / Argo (scalabilité, orchestration), quantization / distillation / LoRA / pruning (optimisation), Computer Vision, Small Language Models (SLM).

## Stack

- **Langages / Librairies** : Python, NumPy, Pandas, Scikit-learn, PyTorch / TensorFlow
- **MLOps** : GitLab CI/CD, Docker, MLflow, Comet ML, versioning modèles / datasets
- **LLM / NLP** : LangChain, LangSmith, Semantic Kernel, RAG
- **Déploiement** : Cloud (AWS, GCP ou Azure), serverless ou containers
- **Monitoring & retraining** : dérive, feedback loop, human-in-the-loop

## Soft skills recherchés

Documentation et transmission de bonnes pratiques, autonomie forte, rigueur, collaboration inter-équipes, curiosité pour les usages IA avancés.

## Écarts identifiés (honnêtes, à garder en tête pour l'entretien)

- **MLflow / Comet ML / GitLab CI/CD** : non pratiqués nommément. Le versioning et la traçabilité en production sont assurés autrement (Terraform, branches de déploiement dédiées, capture de révision avant déploiement, commits sous identité de service) — GitHub Actions / Cloud Build plutôt que GitLab CI/CD.
- **Kubeflow / Argo** : pas d'expérience directe (Kubernetes vu en formation, pas en production). Orchestration réelle faite via Cloud Run (Service + Jobs) et Cloud Tasks.
- **LangSmith / Semantic Kernel** : non utilisés — LangGraph comme équivalent d'orchestration d'agents/RAG.
- **Computer Vision** : présente mais périphérique (embeddings CLIP pour la sélection de keyframes), pas un axe central.
