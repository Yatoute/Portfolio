# Video Mining + Translation Demo

A standalone, public demo of the video-mining pipeline built at Opsci AI —
upload a short video and see the same three stages that run in production
at scale: scene detection, CLIP-based keyframe selection, and WhisperX
transcription. Plus a small text-translation demo using the same engine as
the production cost-optimized translation service.

## Why this exists

The production Cloud Functions (`../../opsci-internship/gcp-fct-video-*`)
are pure processing units — they read/write GCS and Postgres, no UI. There
were internal Flask viewers (`../../opsci-internship/interfaces-de-tests-chez-opsci/`)
but those only *display* results already computed and stored in the
company's database; they don't run the pipeline themselves and can't work
outside that environment. This service **runs the actual pipeline
algorithms** — vendored from the production code — directly on an uploaded
file, in memory, with no database or cloud storage involved.

## What's vendored vs. rebuilt

| Module | Origin |
|---|---|
| `pipeline/scenes.py` | Copied from `gcp-fct-video-scene-detection/utils/video_scene.py`, unchanged except a shorter default `min_scene_duration` (demo clips are short) |
| `pipeline/keyframes.py` | Copied verbatim from `gcp-fct-video-keyframes-selection/utils/keyframes.py` (HSV histogram distinctness + CLIP embeddings + Farthest Point Sampling) |
| `pipeline/transcription.py` | Adapted from `gcp-fct-video-transcription/utils/transcription.py` — same `transcribe_video`/`add_scenes_transcripts` logic, but the LRU/memory-pressure eviction cache for alignment models is dropped (built for a long-lived multi-tenant instance; irrelevant for a short-lived single-video demo process) |
| `pipeline/translation.py` | Uses Argos Translate's standard high-level API (same underlying CTranslate2/OPUS-MT models as `gcp-fct-libretranslate-v2`). The production request-fusion batching layer (`batch_coordinator.py`) isn't reproduced — it only matters at concurrent-request scale, not for one interactive demo call |
| `app.py`, `jobs.py`, `templates/` | New — FastAPI service, background job queue, and UI built for this demo. Not present in production (there, orchestration is via Cloud Tasks + Postgres) |

`gcp-fct-video-coordination` (cross-video reprised-sequence detection) is
**not** included — its premise (comparing a video against a corpus of many
others) doesn't map onto a single-upload demo.

## Architecture

```
POST /api/upload
   │  (validates duration/size, rejects with 413 if over limit)
   ▼
JobManager (in-memory queue, depth-capped at 2)
   │  single background worker — never more than one WhisperX +
   │  OpenCLIP run in memory at a time
   ▼
pipeline.orchestrator.process_video()
   ├─► scenes.detect_scenes()            (PySceneDetect)
   ├─► keyframes.compute_scene_data()    (per scene, HSV + CLIP)
   ├─► keyframes.select_video_keyframes() (global FPS dedup)
   └─► transcription.transcribe_video()  (WhisperX, CPU int8)
   ▼
GET /api/jobs/{id}            — poll status/progress
GET /api/jobs/{id}/result     — scenes + keyframes + transcript, once done
GET /result/{id}              — unified HTML viewer (video + scene cards)
```

## Constraints (by design, not accident)

- **CPU-only, one video at a time.** WhisperX runs in `int8` mode on CPU
  (same code path as production when no GPU is available). No GPU rental
  for this demo — see the portfolio's `video-mining-pipeline` case study
  for the numbers on how much faster/cheaper the production GPU path is.
- **Upload caps**: `VIDEOMINING_MAX_UPLOAD_MB` (default 25MB) and
  `VIDEOMINING_MAX_DURATION_S` (default 180s), enforced before any
  processing starts.
- **Rate limiting**: per-IP, via `slowapi` — `VIDEOMINING_RATE_LIMIT_UPLOAD`
  (default 5/hour) and `VIDEOMINING_RATE_LIMIT_TRANSLATE` (30/hour).
- **Single uvicorn worker.** The job queue and warm model cache
  (`pipeline/orchestrator.py`'s module-level `_clip_selector` /
  `_whisper_model`) are per-process state — running multiple workers would
  multiply memory usage and break `/api/jobs/{id}` lookups across
  processes that don't share memory.

## Local development

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
uvicorn app:app --reload --port 8080
```

First request will download OpenCLIP (ViT-B-32, ~350MB) and the WhisperX
model + alignment model for whatever language is detected — expect a slow
first run, faster after (models are cached to disk).

## Deploying (Fly.io)

```bash
flyctl launch --no-deploy
flyctl volumes create videomining_storage --size 2 --region cdg
flyctl deploy
```

4GB RAM is configured in `fly.toml` (`shared-cpu-2x`) to hold OpenCLIP +
WhisperX + the request buffer simultaneously — adjust after watching real
memory usage in `flyctl status`/`flyctl logs`.
