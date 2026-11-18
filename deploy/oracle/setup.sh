#!/usr/bin/env bash
# One-time install on an Oracle Ubuntu 24.04 VM (ARM or x86), run as user `ubuntu`. Safe to re-run.
# Usage: SITE=ournivesh.duckdns.org CLERK_FAPI=striking-stingray-9664.clerk.accounts.dev \
#        CLERK_PK=pk_test_... bash setup.sh [git-url]
set -euo pipefail
: "${SITE:?set SITE, e.g. ournivesh.duckdns.org}"
: "${CLERK_FAPI:?set CLERK_FAPI, the Clerk Frontend API host}"
: "${CLERK_PK:?set CLERK_PK, the Clerk publishable key (pk_...)}"
REPO=${1:-https://github.com/bikash-barnwal/Nivesh.git}
BRANCH=${BRANCH:-main}

# --- packages: Python 3.12 (system), Node 20, uv, Caddy, security basics -----------------------------------------
sudo apt-get update
sudo apt-get install -y curl git ca-certificates gpg sqlite3 python3.12 python3.12-venv \
  debian-keyring debian-archive-keyring apt-transport-https fail2ban unattended-upgrades netfilter-persistent
sudo dpkg-reconfigure -f noninteractive unattended-upgrades
command -v node >/dev/null || { curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt-get install -y nodejs; }
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin sh
if ! command -v caddy >/dev/null; then
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | sudo gpg --dearmor -o /usr/share/keyrings/caddy.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt | sudo tee /etc/apt/sources.list.d/caddy.list >/dev/null
  sudo apt-get update && sudo apt-get install -y caddy
fi

# --- SSH: keys only, no root login ----------------------------------------------------------------------------------
sudo tee /etc/ssh/sshd_config.d/90-nivesh.conf >/dev/null <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
EOF
sudo systemctl reload ssh || sudo systemctl reload sshd

# --- host firewall: Oracle images reject everything but SSH; open 80/443 only --------------------------------------
for port in 80 443; do
  sudo iptables -C INPUT -p tcp --dport "$port" -m state --state NEW -j ACCEPT 2>/dev/null ||
    sudo iptables -I INPUT 5 -p tcp --dport "$port" -m state --state NEW -j ACCEPT
done
sudo netfilter-persistent save

# --- service user, code, data ---------------------------------------------------------------------------------------
id nivesh >/dev/null 2>&1 || sudo useradd --system --home /nonexistent --shell /usr/sbin/nologin nivesh
sudo mkdir -p /opt/nivesh && sudo chown ubuntu:ubuntu /opt/nivesh
[ -d /opt/nivesh/.git ] || git clone --branch "$BRANCH" "$REPO" /opt/nivesh
sudo mkdir -p /opt/nivesh/data && sudo chown -R nivesh:nivesh /opt/nivesh/data && sudo chmod 750 /opt/nivesh/data

# --- config and secrets (created once, never overwritten) -----------------------------------------------------------
sudo mkdir -p /etc/nivesh && sudo chmod 750 /etc/nivesh && sudo chown root:nivesh /etc/nivesh
if [ ! -f /etc/nivesh/nivesh.env ]; then
  master=$(python3 -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())")
  sed -e "s|^NIVESH_KEYS_MASTER_KEY=.*|NIVESH_KEYS_MASTER_KEY=$master|" \
      -e "s|^NIVESH_AUTHORIZED_PARTIES=.*|NIVESH_AUTHORIZED_PARTIES=[\"https://$SITE\"]|" \
      -e "s|^NIVESH_CLERK_JWKS_URL=.*|NIVESH_CLERK_JWKS_URL=https://$CLERK_FAPI/.well-known/jwks.json|" \
      /opt/nivesh/deploy/oracle/nivesh.env.example | sudo tee /etc/nivesh/nivesh.env >/dev/null
  unset master
  echo ">> Created /etc/nivesh/nivesh.env. Add your Clerk user id to NIVESH_ALLOWED_USER_IDS, then run update.sh"
fi
sudo chown root:nivesh /etc/nivesh/nivesh.env && sudo chmod 640 /etc/nivesh/nivesh.env
printf 'NIVESH_SITE=%s\nNIVESH_CLERK_FAPI=%s\n' "$SITE" "$CLERK_FAPI" | sudo tee /etc/nivesh/caddy.env >/dev/null
printf 'VITE_CLERK_PUBLISHABLE_KEY=%s\n' "$CLERK_PK" | sudo tee /etc/nivesh/frontend.env >/dev/null
sudo chmod 644 /etc/nivesh/caddy.env /etc/nivesh/frontend.env

# --- Caddy reads the site name from caddy.env -----------------------------------------------------------------------
sudo mkdir -p /etc/systemd/system/caddy.service.d /var/log/caddy
printf '[Service]\nEnvironmentFile=/etc/nivesh/caddy.env\n' | sudo tee /etc/systemd/system/caddy.service.d/nivesh.conf >/dev/null
sudo chown caddy:caddy /var/log/caddy

# --- nightly backup of the app database (users' data and encrypted keys), 14 days kept -----------------------------
sudo tee /etc/cron.d/nivesh-backup >/dev/null <<'EOF'
30 2 * * * nivesh mkdir -p /opt/nivesh/data/backups && sqlite3 /opt/nivesh/data/app.sqlite3 ".backup /opt/nivesh/data/backups/app-$(date +\%F).sqlite3" && find /opt/nivesh/data/backups -name 'app-*.sqlite3' -mtime +14 -delete
EOF

sudo systemctl daemon-reload
sudo systemctl enable nivesh-api caddy fail2ban >/dev/null 2>&1 || true
bash /opt/nivesh/deploy/oracle/update.sh
