# Image Playwright Python — Python + Chromium déjà installés (même base que scrapper-facebook)
FROM mcr.microsoft.com/playwright/python:v1.60.0-noble

WORKDIR /app

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
ENV PYTHONUNBUFFERED=1

# Node.js 20 + Claude Code CLI
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs git netcat-openbsd xvfb && \
    rm -rf /var/lib/apt/lists/*
RUN npm install -g @anthropic-ai/claude-code

# gcpOpsci depuis les sources locales (même pattern que scrapper-facebook)
COPY gcp-python-dependencies /gcp-python-dependencies
RUN pip install --no-cache-dir /gcp-python-dependencies

# Dépendances Python des scripts de monitoring (FastAPI, uvicorn)
COPY functions/admin/gcp-fct-monitoring-agent/requirements.txt ./requirements-monitoring.txt
RUN pip install --no-cache-dir -r requirements-monitoring.txt

# Dépendances Node
COPY functions/admin/gcp-fct-monitoring-agent/package*.json ./
RUN npm install

# Utilisateur non-root — requis par --dangerously-skip-permissions
RUN useradd -m agent && \
    mkdir -p /workspace/scripts /home/agent/.claude/commands && \
    chown -R agent:agent /workspace /app /home/agent

# Scripts de monitoring baked dans l'image
COPY --chown=agent:agent functions/admin/gcp-fct-monitoring-agent/scripts/ /workspace/scripts/monitoring/

# Config scrappers (cf_url, default_queue par CF)
COPY --chown=agent:agent functions/admin/gcp-fct-monitoring-agent/config/ /app/config/

# Commande Claude Code globale (dans le home de l'utilisateur non-root)
COPY --chown=agent:agent functions/admin/gcp-fct-monitoring-agent/commands/ /home/agent/.claude/commands/

# Serveur (Service) + runner (Job) + entrypoint
COPY --chown=agent:agent functions/admin/gcp-fct-monitoring-agent/server.js functions/admin/gcp-fct-monitoring-agent/job-runner.js ./
COPY functions/admin/gcp-fct-monitoring-agent/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER agent
ENV HOME=/home/agent

EXPOSE 8080

ENV WORK_DIR=/workspace

# entrypoint.sh fait le sparse-checkout puis `exec "$@"`. CMD par défaut = le Service (server.js).
# Le Job Cloud Run surcharge les args → `node /app/job-runner.js`.
ENTRYPOINT ["/entrypoint.sh"]
CMD ["node", "/app/server.js"]
