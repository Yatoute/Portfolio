Bonjour,

Votre besoin d'industrialiser et sécuriser la mise en production de modèles IA — pas seulement les entraîner — correspond à ce que j'ai fait chez Opsci AI : un agent de monitoring qui surveille en continu des pipelines de scraping en production, diagnostique la cause exacte d'une panne (dérive de schéma, session expirée, rate-limiting), corrige le code, puis valide systématiquement en local avant tout déploiement — reproduction du bug, vérification des champs corrigés, capture de la révision courante, déploiement sur branche dédiée. C'est directement la logique de pipeline traçable et de feedback loop que vous décrivez, avec du human-in-the-loop réel : confirmation obligatoire avant toute action irréversible, et un mécanisme de repli qui privilégie la résolution automatique avant d'escalader vers un humain.

Sur la partie LLM/RAG, j'ai construit un agent LangGraph (ChatPDF) avec recherche sémantique FAISS et navigation de documents, et je fine-tune des modèles open-source en LoRA/QLoRA (Qwen via LLaMA-Factory) — l'optimisation par quantization que vous mentionnez, je la pratique aussi côté inférence : mon moteur de traduction en process (CTranslate2, quantization int8) a réduit les coûts d'infrastructure GCP de 90% et multiplié la vitesse par 77, une fois passé en production réelle sur des dizaines de milliers de documents/jour.

Mes outils de CI/CD et de versioning de référence sont GitHub Actions / Cloud Build / Terraform plutôt que GitLab CI/CD / MLflow / Comet ML, mais la discipline qu'ils demandent — pipelines reproductibles, traçabilité, rollback maîtrisé — est la même, et directement transférable.

Je vous invite à consulter mon portfolio (yatoute.vercel.app) pour découvrir ces réalisations en détail, avec des démos testables en direct.

Disponible pour échanger quand vous le souhaitez.

Cordialement,
Yatoute Mintoama
