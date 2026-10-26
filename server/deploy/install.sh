#!/usr/bin/env bash
# One-shot install on a fresh Ubuntu 22.04/24.04 VPS. Run as root:  bash install.sh license.example.com
# Afterwards: python3 /opt/mfh/server/license_server.py --list  (etc., see docs/LICENSE_SERVER_SETUP_HE.md)
set -euo pipefail
DOMAIN="${1:?usage: install.sh <your-domain>}"
apt-get update -y && apt-get install -y python3 python3-pip debian-keyring debian-archive-keyring apt-transport-https curl
pip3 install --break-system-packages cryptography
id -u mfh &>/dev/null || useradd --system --home /opt/mfh --shell /usr/sbin/nologin mfh
mkdir -p /opt/mfh/server
cp "$(dirname "$0")/../license_server.py" /opt/mfh/server/
cd /opt/mfh/server
[ -f private_key.pem ] || sudo -u mfh python3 license_server.py --init      # keys + database, owned by mfh
chown -R mfh:mfh /opt/mfh && chmod 600 private_key.pem
cp "$(dirname "$0")/mfh-license.service" /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now mfh-license
# Caddy (HTTPS)
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
apt-get update -y && apt-get install -y caddy
sed "s/license.example.com/${DOMAIN}/" "$(dirname "$0")/Caddyfile" > /etc/caddy/Caddyfile
systemctl reload caddy || systemctl restart caddy
# firewall: only ssh + http(s)
if command -v ufw &>/dev/null; then ufw allow OpenSSH; ufw allow 80; ufw allow 443; ufw --force enable; fi
echo
echo "=== done. Public key to put in the Friends build: ==="
cat /opt/mfh/server/public_key.txt
echo "=== test: curl https://${DOMAIN}/v1/health ==="
