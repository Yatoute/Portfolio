# Video Mining Quality App

Interface de test pour évaluer la qualité des résultats produits par les Cloud Functions du pipeline vidéo :

- **`video-scene-transcription`** — pertinence du découpage en scènes et qualité du transcript
- **`video-keyframes-selection`** — représentativité des keyframes sélectionnés par scène

## Prérequis

- Python (venv du projet)
- `ffmpeg` installé sur la machine
- `gcloud` authentifié pour l'accès à GCS (`gcloud auth application-default login`)
- Cloud SQL Proxy pour l'accès à la base de données

## Lancement

### 1. Démarrer le proxy Cloud SQL

Dans un terminal dédié :

```bash
cloud_sql_proxy -instances=demo-project:europe-west1:opsci-bdd=tcp:5432
```

Laisser ce terminal ouvert pendant toute la session.

### 2. Configurer les variables d'environnement

Copier le template et remplir les credentials :

```bash
cp .env.template .env
```

Éditer `.env` avec les valeurs réelles (`DB_USER`, `DB_PASS`).

### 3. Installer les dépendances

```bash
pip install -r requirements.txt
```

> `gcpOpsci` est installé depuis GCP Artifact Registry via le `--extra-index-url` défini dans `requirements.txt`.

### 4. Lancer l'application

```bash
cd notebooks/video-mining/keyframes-quality-app
python app.py
```

L'application est accessible sur [http://localhost:5001](http://localhost:5001).

## Endpoints

| URL | Description |
|-----|-------------|
| `/keyframes?doc_id=<id>` | Grille des keyframes par scène + transcription |
| `/keyframes?url=gs://...&importation_id=<id>` | Idem, recherche par URL GCS |
| `/video?doc_id=<id>` | Vidéo originale vs reconstruite côte à côte |
| `/video?url=gs://...&importation_id=<id>` | Idem, recherche par URL GCS |

## Recherche d'un document

Deux modes exclusifs dans le formulaire :

- **URI GCS + importation_id** — `blob_url` et `importation_id` obligatoires ensemble (`importation_id` est indexé en BDD, accélère la recherche)
- **Doc ID** — recherche directe par `documents.id` (clé primaire, le plus rapide)