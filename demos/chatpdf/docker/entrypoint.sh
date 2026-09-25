#!/usr/bin/env bash
set -euo pipefail

: "${PORT:=8080}"
: "${PROXY_PORT:=${PORT}}"
export PROXY_PORT

rm -f /etc/nginx/conf.d/default.conf

envsubst '$PROXY_PORT' < /etc/nginx/templates/nginx.conf.template > /etc/nginx/conf.d/render.conf

# echo "----- Rendered Nginx conf -----"
# sed -n '1,80p' /etc/nginx/conf.d/render.conf
# echo "-------------------------------"

nginx -t

exec supervisord -c /etc/supervisord.conf