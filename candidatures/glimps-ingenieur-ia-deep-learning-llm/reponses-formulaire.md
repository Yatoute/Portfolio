# Réponses au formulaire de candidature

**Pourquoi ce poste vous intéresse-t-il ?**

Ce poste combine deux choses que je cherche précisément : du deep learning appliqué à un problème concret et exigeant (la détection de malwares par la compréhension automatique du code), et une vraie culture d'industrialisation — reproductibilité des entraînements, qualité de code, intégration produit — plutôt qu'un rôle cantonné aux notebooks. En stage chez Opsci AI, j'ai justement passé mon temps sur cette frontière entre recherche et production : fine-tuner des modèles, mais aussi les faire tourner de façon fiable, monitorée et maintenable.

La dimension cybersécurité m'intéresse particulièrement parce qu'elle rejoint un exercice que j'ai déjà pratiqué sans l'appeler comme ça : sur un agent de monitoring qui diagnostique des pannes en production, j'ai dû reconstruire par rétro-ingénierie des schémas d'API non documentés qui dérivaient, intercepter du trafic réseau pour retrouver des identifiants de requête devenus obsolètes, et contourner des mécanismes anti-bot — une démarche de compréhension d'un système opaque assez proche, dans l'esprit, de ce que fait GLIMPS sur du code malveillant, même si le terrain (API web vs binaires) est différent.

Enfin, les axes LLM agentiques et RL appliqué aux LLM correspondent à la direction que je veux prendre : j'ai déjà construit des systèmes agentiques en production (orchestration d'outils, planification, garde-fous), et le RL/RLHF est la brique que je n'ai pas encore pratiquée en profondeur mais que je veux développer — ce poste est un contexte concret pour le faire, avec une équipe IA en place pour progresser vite.

**Quelles compétences souhaitez-vous mettre en avant pour ce poste ?**

- **Fine-tuning de LLM** : SFT et LoRA/QLoRA sur modèles open-source (Qwen via LLaMA-Factory), implémentation d'un GPT-2 from scratch en PyTorch (attention, embeddings, weight tying) avec intégration des poids officiels — une compréhension de l'architecture qui va au-delà de l'usage d'une librairie de fine-tuning.
- **Systèmes LLM agentiques en production** : conception d'un agent qui diagnostique et corrige lui-même des pannes de pipelines de production (raisonnement multi-étapes, orchestration d'outils — Playwright, requêtes base de données, déploiement —, garde-fous explicites, validation obligatoire avant toute action irréversible), et d'un agent RAG (LangGraph, recherche sémantique FAISS, navigation de documents).
- **Discipline d'industrialisation** : Docker, CI/CD, Git, boucle de validation systématique avant déploiement (reproduction du bug en local, vérification des correctifs, capture de révision), traçabilité complète des changements — exactement la logique de reproductibilité et de qualité de code que vous décrivez.
- **Rigueur expérimentale / Data Science** : préparation de données à l'échelle (pipelines GCP pour la collecte de données sociales), tuning empirique documenté (taille de batch GPU testée à plusieurs paliers avec mesure systématique avant/après), analyse de résultats chiffrée plutôt qu'impressionniste.
- **Environnement Linux / Python quotidien** : stack de développement Linux, Python comme langage principal sur l'ensemble des projets cités.

Je reste honnête sur deux points : je n'ai pas encore pratiqué le RL/RLHF en production, et mon expérience directe en cybersécurité est nulle — mon point de contact le plus proche est la rétro-ingénierie d'API non documentées décrite plus haut. Ce sont des axes que je veux construire, pas des compétences que je revendique déjà.
