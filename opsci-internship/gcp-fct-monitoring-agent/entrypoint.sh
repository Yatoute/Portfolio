#!/bin/bash
set -e

REPO_URL="${REPO_URL:-}"
REPO_DIR="/workspace"

## Sparse clone des CF scrappers depuis GitHub
if [ -z "$REPO_URL" ]; then
  echo "⚠️  REPO_URL non défini — /workspace utilisé tel quel (mode local)"
else
  AUTHENTICATED_URL=$(echo "$REPO_URL" | sed "s|https://|https://x-access-token:${GITHUB_TOKEN}@|")

  if [ -d "$REPO_DIR/.git" ]; then
    echo "🔄 git pull $REPO_DIR"
    git -C "$REPO_DIR" pull --ff-only
  else
    echo "⬇️  Sparse checkout de $REPO_URL → $REPO_DIR"
    cd "$REPO_DIR"
    git -c init.defaultBranch=main init
    git remote add origin "$AUTHENTICATED_URL"
    git fetch --depth 1 --filter=blob:none origin main

    git sparse-checkout init --cone

    RESOLVED=$(git ls-tree --name-only -r FETCH_HEAD | grep -E "^functions/scrapper/gcp-fct-scrapper-" | sed 's|functions/scrapper/||')
    DIRS="${SPARSE_DIRS:-$RESOLVED}"

    echo "📂 Sparse dirs : $DIRS"
    # shellcheck disable=SC2086
    git sparse-checkout set $DIRS

    git checkout -b main FETCH_HEAD
    git branch --set-upstream-to=origin/main main

    echo "✅ Sparse checkout OK : $(git rev-parse --short HEAD)"
  fi

  ## Git identity pour les commits de l'agent
  git -C "$REPO_DIR" config user.name "monitoring-agent"
  git -C "$REPO_DIR" config user.email "monitoring-agent@demo-project.iam.gserviceaccount.com"

fi

## Lance la commande du conteneur (CMD/args) : `node server.js` (Service) ou `node job-runner.js` (Job).
exec "$@"
