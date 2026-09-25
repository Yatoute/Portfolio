# ChatPDF

An agentic PDF RAG chatbot. Upload a PDF (OCR included for scanned
documents) and chat with it — the agent decides how to gather evidence
itself rather than doing a flat top-k retrieval.

## How it works

Built with **LangGraph**: the agent has tools rather than a fixed
retrieval pipeline —

- `semantic_search` — FAISS similarity / MMR search over chunks.
- `fetch_next` / `fetch_previous` / `fetch_window` — chunk-graph
  navigation, to pull neighboring context when an idea spans multiple
  chunks.
- `fetch_page` — jump to a page's full text.
- `list_documents` — multi-document session awareness.

Retrieval is capped at 2 rounds before the agent is forced to answer, and
long conversations are automatically summarized to keep the context window
bounded.

Model providers are pluggable — OpenAI (`gpt-5-nano` / `text-embedding-3-small`
by default) or a local Ollama instance — via a `ModelsFactory`.

## Architecture

FastAPI (`src/chatpdf/api`) + Streamlit (`src/chatpdf/ui`) + nginx, run
together in one container via `supervisord`. nginx exposes a single port,
proxying `/` to Streamlit and `/api/` to FastAPI (needed for Streamlit's
websocket upgrade to work behind a reverse proxy).

```
nginx :8080
 ├── /          → Streamlit :8501
 └── /api/      → FastAPI   :8000
```

On first boot, if no index exists yet in storage, the API seeds itself
with a bundled example PDF (`examples/preload/`) so a fresh deployment is
immediately usable without requiring an upload (see
`src/chatpdf/api/preload.py`).

## Local development

```bash
cp .env.template .env   # fill in OPENAI_API_KEY (or configure Ollama)
pip install -e ".[api,ui,dev]"
python -m chatpdf.api.main   # API on :8000
streamlit run src/chatpdf/ui/app.py   # UI on :8501, separate terminal
```

## Configuration

All API-layer settings are read from environment variables (`CHATPDF_*`),
see `src/chatpdf/api/config.py`:

| Variable | Default | Purpose |
|---|---|---|
| `CHATPDF_STORAGE_DIR` | `./storage` | Where uploads + FAISS indexes live |
| `CHATPDF_ALLOWED_ORIGINS` | `*` | CORS — set to the real frontend domain in prod |
| `CHATPDF_RATE_LIMIT_UPLOAD` | `3/day` | Per-IP upload limit |
| `CHATPDF_RATE_LIMIT_CHAT` | `20/hour` | Per-IP chat message limit |
| `CHATPDF_RATE_LIMIT_SESSION` | `10/hour` | Per-IP session creation limit |
| `CHATPDF_PRELOAD_EXAMPLE` | `true` | Seed a demo document on first boot |

Model provider settings (`OPENAI_API_KEY`, `OLLAMA_BASE_URL`, etc.) are in
`.env.template`.

## Deploying (Fly.io)

```bash
flyctl launch --no-deploy          # creates the app from fly.toml
flyctl volumes create chatpdf_storage --size 3 --region cdg
flyctl secrets set OPENAI_API_KEY=sk-... CHATPDF_ALLOWED_ORIGINS=https://<portfolio-domain>
flyctl deploy
```

Use a **dedicated OpenAI key with a spend cap** for the public demo, not
your personal key — the rate limits above bound request volume, but a hard
budget cap on the key itself is the real safety net.
