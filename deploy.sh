#!/bin/bash
# deploy.sh — Einmaliges Server-Setup auf Ubuntu 24.04 (Hetzner CX22)
# Aufruf: bash deploy.sh <DOMAIN>
# Beispiel: bash deploy.sh thermal.juflie.app
set -euo pipefail

DOMAIN="${1:?Usage: bash deploy.sh <DOMAIN>}"
REPO_DIR="/opt/thermal-backend"

echo "=== [1/6] System-Pakete ==="
apt-get update -qq
apt-get install -y -qq curl git ufw

echo "=== [2/6] Docker installieren ==="
if ! command -v docker &>/dev/null; then
    curl -fsSL https://get.docker.com | sh
fi
# Docker Compose Plugin (v2)
docker compose version &>/dev/null || apt-get install -y docker-compose-plugin

echo "=== [3/6] Firewall ==="
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "=== [4/6] Repo klonen / aktualisieren ==="
if [ -d "$REPO_DIR/.git" ]; then
    git -C "$REPO_DIR" pull --ff-only
else
    git clone https://github.com/DEIN_ORG/thermal-backend.git "$REPO_DIR"
fi
cd "$REPO_DIR"

echo "=== [5/6] .env.prod anlegen (falls nicht vorhanden) ==="
if [ ! -f .env.prod ]; then
    cp .env.prod.example .env.prod
    # Zufälliges Passwort generieren
    PG_PASS=$(openssl rand -hex 24)
    sed -i "s/SICHERES_PASSWORT_HIER_EINTRAGEN/$PG_PASS/g" .env.prod
    sed -i "s/<DOMAIN>/$DOMAIN/g" .env.prod
    echo ""
    echo "  ⚠️  .env.prod wurde angelegt. Bitte prüfen:"
    echo "  nano $REPO_DIR/.env.prod"
    echo ""
fi

# Domain im nginx-Config eintragen
sed -i "s/<DOMAIN>/$DOMAIN/g" nginx/thermal.conf

echo "=== [6/6] TLS-Zertifikat (Let's Encrypt) ==="
if [ ! -d "/etc/letsencrypt/live/$DOMAIN" ]; then
    # Certbot standalone (Port 80 muss frei sein)
    apt-get install -y -qq certbot
    certbot certonly --standalone \
        --non-interactive --agree-tos \
        --email "admin@$DOMAIN" \
        -d "$DOMAIN"
    # Auto-Renewal via systemd-Timer (bereits durch Certbot eingerichtet)
fi

echo "=== Services starten ==="
docker compose -f docker-compose.prod.yml pull --quiet
docker compose -f docker-compose.prod.yml up -d --build

echo ""
echo "✅ Deployment abgeschlossen!"
echo "   https://$DOMAIN/healthz prüfen"
