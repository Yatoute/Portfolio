# Senior AI Engineer — Mirakl

- **Contrat** : CDI, Paris ou Bordeaux, 4j/semaine sur site
- **Lieu** : Bordeaux, France (ou Paris)
- **Rémunération** : 65k€-85k€ brut/an (package)
- **Publiée / actualisée** : non spécifiée
- **Candidature préparée le** : 2026-08-22
- **Formulaire déjà en partie rempli par l'utilisateur** (nom, email, téléphone, localisation, CV, questions oui/non) — seule la lettre de motivation restait à faire.

## Qui sont-ils

Mirakl — éditeur de plateformes marketplace B2B/B2C (Mirakl Platform, Connect, Ads, Nexus). 750+ employés, sièges à Boston et Paris, 450+ marketplaces clientes (Macy's, Decathlon, Carrefour, Asos, Airbus Helicopters). Virage produit vers l'IA agentique ("Agentic Commerce").

## Le rôle

Poste au croisement Software Engineering / ML / GenAI : construire des systèmes IA production-grade (agents, RAG, multimodal) utilisés en interne et par les clients Mirakl. Transformation des façons de travailler des équipes internes + impact direct sur le volet agentique des produits SaaS.

## Missions

1. **Concevoir & construire** des systèmes IA (LLMs, RAG, agents, multimodal, frameworks d'évaluation), architecturer des workflows agentiques complexes (tools, mémoire, retrieval, planning, guardrails).
2. **Intégrer sur toute la stack** : connecter les agents IA aux systèmes internes (CRM, data platform, Drive, Jira, support, bases de connaissance), concevoir la couche d'orchestration modèles/API/outils/retrieval, garantir fiabilité/observabilité/scalabilité.
3. **Itérer pour l'impact** : pilotes/expérimentations avec les équipes produit/business, mesure d'usage, coûts, latence, performance.
4. **Qualité, sécurité, fiabilité** : frameworks d'évaluation, guardrails, monitoring, conformité gouvernance/sécurité des données.
5. **Adoption & enablement** : standards d'ingénierie IA, frameworks réutilisables, évangélisation des façons de travailler IA-natives.

## Profil recherché

**Indispensable** : 5+ ans d'expérience en Software Engineering / ML Engineering / Applied AI, solides fondamentaux Python et ingénierie logicielle, expérience concrète de systèmes IA en production (LLMs, RAG, agents), conception de workflows agentiques multi-étapes et de systèmes de retrieval, évaluation de systèmes IA, déploiement/exploitation de services en production sur le cloud, anglais professionnel solide.

**Apprécié** : mentionner un lien GitHub dans le CV.

## Stack

- **Langages** : Python, SQL
- **IA** : OpenAI, Anthropic, Gemini, Mistral, modèles open-source
- **Agentique & Retrieval** : LangGraph, LangChain, LlamaIndex, MCP, bases vectorielles
- **Infra** : AWS ou GCP, Docker, Kubernetes

## Process d'entretien

1. Appel téléphonique recruteur (30-40 min)
2. Entretien technique avec Data Science + Engineering (1h)
3. 2 × entretiens "STAR" valeurs/culture (45 min chacun)

## Écarts identifiés (honnêtes, à garder en tête pour l'entretien)

- **Séniorité (5+ ans)** : c'est l'écart le plus important. L'expérience réelle couvre ~1 an et demi cumulés sur deux stages (stage AI Engineer mars-sept 2026, stage Data Engineer/NLP juin-sept 2025), avec un diplôme obtenu en 2026 — pas 5 ans en poste. Le dossier se défend sur la **preuve par la production** (agent de monitoring autonome en prod, -90%/×77 sur un moteur de traduction) plutôt que sur la durée, mais c'est un vrai décalage à assumer en entretien, pas à minimiser.
- **MCP (Model Context Protocol)** : non utilisé jusqu'ici. LangGraph avec tool-calling a été le pattern d'orchestration d'agents pratiqué (ChatPDF).
- **Bases vectorielles managées** : FAISS en local, pas de Pinecone/Weaviate/Qdrant/pgvector en prod.
- **Kubernetes** : vu en formation, pas d'exploitation en production (orchestration réelle faite via GCP Cloud Run + Cloud Tasks).
- **Mistral / Gemini** : expérience surtout OpenAI + Anthropic + modèles open-source (Qwen, GPT-2), pas Mistral/Gemini spécifiquement.
