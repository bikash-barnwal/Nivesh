#!/usr/bin/env bash
# Redeploy after pushing new code: one command, run on the VM as `ubuntu`. Data and /etc/nivesh are never touched.
set -euo pipefail
cd /opt/nivesh
git pull --ff-only

# Server: system Python 3.12 (the service's sandbox cannot read uv's per-user Python under /home)
(cd server && UV_PYTHON_PREFERENCE=only-system uv sync --frozen --no-dev --python python3.12)

# Client: the Clerk publishable key is baked in at build time
(cd client && npm ci --no-audit --no-fund && set -a && . /etc/nivesh/frontend.env && set +a && npm run build)

sudo cp deploy/oracle/nivesh-api.service /etc/systemd/system/
sudo cp deploy/oracle/Caddyfile /etc/caddy/Caddyfile
sudo systemctl daemon-reload
sudo env $(cat /etc/nivesh/caddy.env) caddy validate --config /etc/caddy/Caddyfile >/dev/null
sudo systemctl restart nivesh-api
sudo systemctl reload-or-restart caddy

for _ in $(seq 1 30); do curl -fsS http://127.0.0.1:15000/health >/dev/null 2>&1 && break; sleep 2; done
curl -fsS http://127.0.0.1:15000/health && echo
. /etc/nivesh/caddy.env
echo "OK: https://$NIVESH_SITE"
