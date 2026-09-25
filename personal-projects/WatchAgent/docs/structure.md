# Structure globale recommandée

```text
watchagent/
├── README.md
├── pyproject.toml
├── .env.example
├── docker-compose.yml
├── Makefile
├── configs/
│   ├── app.yaml
│   ├── logging.yaml
│   ├── models.yaml
│   └── cameras.example.yaml
├── scripts/
│   ├── run_api.py
│   ├── run_worker.py
│   ├── simulate_rtsp.sh
│   └── seed_demo_data.py
├── deployments/
│   ├── docker/
│   │   ├── api.Dockerfile
│   │   └── worker.Dockerfile
│   └── k8s/
│       ├── api.yaml
│       ├── worker.yaml
│       ├── redis.yaml
│       └── postgres.yaml
├── data/
│   ├── samples/
│   ├── snapshots/
│   └── clips/
├── notebooks/
│   ├── rtsp_debug.ipynb
│   ├── scene_detection_experiments.ipynb
│   └── keyframe_selection_experiments.ipynb
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
└── src/
    └── watchagent/
        ├── __init__.py
        ├── settings.py
        ├── logging_config.py
        ├── constants.py
        ├── exceptions.py
        │
        ├── api/
        │   ├── __init__.py
        │   ├── app.py
        │   ├── dependencies.py
        │   ├── routers/
        │   │   ├── health.py
        │   │   ├── cameras.py
        │   │   ├── streams.py
        │   │   ├── events.py
        │   │   └── alerts.py
        │   ├── schemas/
        │   │   ├── camera.py
        │   │   ├── event.py
        │   │   └── alert.py
        │   └── services/
        │       ├── camera_service.py
        │       └── event_query_service.py
        │
        ├── core/
        │   ├── __init__.py
        │   ├── models/
        │   │   ├── frame_packet.py
        │   │   ├── camera_config.py
        │   │   ├── scene.py
        │   │   ├── event.py
        │   │   └── alert.py
        │   ├── ports/
        │   │   ├── frame_source.py
        │   │   ├── event_repository.py
        │   │   ├── alert_sender.py
        │   │   └── vlm_client.py
        │   └── utils/
        │       ├── time.py
        │       ├── image.py
        │       └── ids.py
        │
        ├── video_streaming/
        │   ├── __init__.py
        │   ├── base_streamer.py
        │   ├── rtsp_streamer.py
        │   ├── file_streamer.py
        │   └── frame_queue.py
        │
        ├── perception/
        │   ├── __init__.py
        │   ├── motion/
        │   │   ├── motion_detector.py
        │   │   └── background_subtractor.py
        │   ├── objects/
        │   │   ├── object_detector.py
        │   │   └── person_detector.py
        │   ├── tracking/
        │   │   └── tracker.py
        │   ├── scenes/
        │   │   ├── scene_segmenter.py
        │   │   ├── scene_state.py
        │   │   └── novelty_scorer.py
        │   └── keyframes/
        │       ├── keyframe_selector.py
        │       └── keyframe_rules.py
        │
        ├── intelligence/
        │   ├── __init__.py
        │   ├── event_builder.py
        │   ├── anomaly_scoring.py
        │   ├── rules_engine.py
        │   ├── decision_engine.py
        │   └── prompt_builder.py
        │
        ├── vlm/
        │   ├── __init__.py
        │   ├── qwen_client.py
        │   ├── schemas.py
        │   ├── parser.py
        │   └── templates/
        │       ├── event_analysis.txt
        │       └── alert_summary.txt
        │
        ├── memory/
        │   ├── __init__.py
        │   ├── short_term_store.py
        │   ├── event_history.py
        │   └── normal_behavior_profile.py
        │
        ├── alerts/
        │   ├── __init__.py
        │   ├── dispatcher.py
        │   ├── email_sender.py
        │   ├── webhook_sender.py
        │   └── notification_policy.py
        │
        ├── storage/
        │   ├── __init__.py
        │   ├── db/
        │   │   ├── base.py
        │   │   ├── models.py
        │   │   ├── repositories/
        │   │   │   ├── camera_repository.py
        │   │   │   ├── event_repository.py
        │   │   │   └── alert_repository.py
        │   │   └── migrations/
        │   ├── blob/
        │   │   ├── local_store.py
        │   │   └── s3_store.py
        │   └── cache/
        │       └── redis_cache.py
        │
        ├── workers/
        │   ├── __init__.py
        │   ├── stream_worker.py
        │   ├── event_worker.py
        │   └── alert_worker.py
        │
        └── monitoring/
            ├── __init__.py
            ├── metrics.py
            ├── healthchecks.py
            └── tracing.py
```

# Comment penser cette architecture

## 1. `video_streaming/`

C’est la couche d’entrée vidéo.

Tu y mets :

* `RTSPFrameStreamer`
* les futurs streamers de fichiers
* la gestion du thread et de la queue
* la logique “toujours garder la frame la plus récente”

C’est la bonne place pour ton module actuel.

## 2. `perception/`

C’est la vision temps réel peu coûteuse.

Tu y mets :

* détection de mouvement
* détection d’objets
* segmentation de scène
* sélection de keyframes

Cette couche ne dépend pas du VLM.

## 3. `intelligence/`

C’est le cerveau décisionnel non-LLM.

Tu y mets :

* règles métier
* score d’anomalie
* décision d’alerte
* construction de l’événement

Exemple :
“personne détectée à 03:00 dans une zone interdite”
→ niveau d’urgence élevé

## 4. `vlm/`

C’est l’intégration Qwen/VLM.

Tu y mets :

* le client vLLM
* les prompts
* le parsing JSON
* les schémas de sortie

Cette séparation est très importante : si demain tu remplaces Qwen par un autre modèle, tu ne casses pas le reste.

## 5. `memory/`

Très utile plus tard.

Tu y mets :

* mémoire court terme
* historique des événements
* profil de normalité d’une caméra

C’est ici que WatchAgent devient “smart”.

## 6. `alerts/`

Couche d’envoi.

Tu y mets :

* email
* webhook
* règles de notification
* priorisation

## 7. `storage/`

Couche de persistance.

Tu y mets :

* base relationnelle pour les événements et caméras
* stockage blob pour snapshots/clips
* cache Redis si besoin

## 8. `api/`

FastAPI ou autre.

Tu y exposes :

* gestion des caméras
* lecture des événements
* consultation des alertes
* health endpoints

## 9. `workers/`

Les traitements asynchrones ou continus.

Par exemple :

* un worker par flux caméra
* un worker pour inférence VLM
* un worker pour dispatcher les alertes

# Structure minimale pour commencer

Tu n’as pas besoin d’implémenter toute la structure dès aujourd’hui. Pour un vrai démarrage propre, je te conseille cette version réduite :

```text
watchagent/
├── README.md
├── pyproject.toml
├── .env.example
├── scripts/
│   └── simulate_rtsp.sh
├── tests/
│   └── unit/
└── src/
    └── watchagent/
        ├── settings.py
        ├── api/
        │   └── app.py
        ├── core/
        │   └── models/
        │       └── frame_packet.py
        ├── video_streaming/
        │   └── rtsp_streamer.py
        ├── perception/
        │   ├── scenes/
        │   │   └── scene_segmenter.py
        │   └── keyframes/
        │       └── keyframe_selector.py
        ├── vlm/
        │   └── qwen_client.py
        ├── intelligence/
        │   └── decision_engine.py
        └── storage/
            └── db/
                └── models.py
```

Ça suffit largement pour un PoC propre.

# Convention de nommage recommandée

Je te conseille :

* dossiers en `snake_case`
* fichiers en `snake_case`
* classes en `PascalCase`
* interfaces ou contrats dans `core/ports/`

Exemples :

* `rtsp_streamer.py`
* `scene_segmenter.py`
* `decision_engine.py`
* `FramePacket`
* `RTSPFrameStreamer`

# Flux logique dans le projet

Le flux idéal de WatchAgent ressemble à ça :

```text
video_streaming/rtsp_streamer.py
    ↓
perception/scenes/scene_segmenter.py
    ↓
perception/keyframes/keyframe_selector.py
    ↓
intelligence/event_builder.py
    ↓
vlm/qwen_client.py
    ↓
intelligence/decision_engine.py
    ↓
alerts/dispatcher.py
    ↓
storage/db + blob
```

# Ce que je te recommande comme premiers fichiers à créer

Pour bien partir, je créerais d’abord :

```text
src/watchagent/settings.py
src/watchagent/core/models/frame_packet.py
src/watchagent/video_streaming/rtsp_streamer.py
src/watchagent/perception/scenes/scene_segmenter.py
src/watchagent/perception/keyframes/keyframe_selector.py
src/watchagent/intelligence/decision_engine.py
src/watchagent/vlm/qwen_client.py
src/watchagent/api/app.py
```

# Proposition d’organisation par phase

## Phase 1 — Ingestion

* `video_streaming/rtsp_streamer.py`
* `core/models/frame_packet.py`

## Phase 2 — Perception légère

* `scene_segmenter.py`
* `keyframe_selector.py`

## Phase 3 — Intelligence

* `event_builder.py`
* `decision_engine.py`

## Phase 4 — VLM

* `qwen_client.py`
* `prompt_builder.py`

## Phase 5 — Produit

* `api/app.py`
* `storage/`
* `alerts/`

# Mon conseil architectural principal

Fais en sorte que :

* la lecture RTSP soit indépendante
* la perception légère soit indépendante du VLM
* le VLM soit remplaçable
* la décision finale soit pilotée par un moteur clair

Autrement dit, ne mélange pas :

* OpenCV
* logique de scène
* prompts Qwen
* accès DB
  dans un seul fichier.

# Mon choix concret pour WatchAgent

Si tu veux une structure sérieuse mais pas trop lourde, prends celle-ci comme base :

```text
src/watchagent/
├── api/
├── core/
├── video_streaming/
├── perception/
├── intelligence/
├── vlm/
├── storage/
└── alerts/
```

C’est probablement le meilleur compromis entre :

* clarté
* évolutivité
* simplicité