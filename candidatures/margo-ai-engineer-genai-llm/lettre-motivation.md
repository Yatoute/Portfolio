Bonjour,

Votre besoin d'orchestration multi-agents et de raisonnement autonome correspond à mon expérience chez Opsci AI : j'ai conçu un agent de monitoring qui diagnostique et corrige lui-même des pipelines de production, en lançant plusieurs sous-agents en parallèle (un par session à reconnecter) et en résolvant des challenges reCAPTCHA sous contrainte de temps réelle — de l'exécution autonome de tâches complexes, pas un chatbot scripté.

Côté RAG, j'ai construit un agent LangGraph (ChatPDF) qui navigue dans les documents via recherche sémantique FAISS, navigation entre chunks et lecture de page, plutôt qu'un simple top-k.

Sur la sélection/fine-tuning de modèles selon coût et performance : j'ai remplacé un appel API externe par un moteur en process (CTranslate2), ce qui a réduit les coûts d'infrastructure de 90% et multiplié la vitesse par 77 ; je fine-tune aussi des modèles open-source (GPT-2, QLoRA sur Qwen).

Ma base vectorielle de référence est FAISS plutôt que Pinecone/Weaviate/Qdrant, mais indexation, recherche sémantique, Docker et CI/CD sont directement transférables.

Je vous invite à consulter mon portfolio (yatoute.vercel.app) pour découvrir ces réalisations en détail, avec des démos testables en direct.

Cordialement,
Yatoute Mintoama
