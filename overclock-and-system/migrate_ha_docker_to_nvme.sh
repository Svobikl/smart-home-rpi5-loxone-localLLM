#!/usr/bin/env bash
set -euo pipefail
# Migrate Home Assistant (/usr/share/hassio) and Docker (/var/lib/docker) to NVMe SSD (/mnt/nvme)

NVME_PART="${1:-/dev/nvme0n1p1}"
mkdir -p /mnt/nvme

if ! mountpoint -q /mnt/nvme; then
  mount "$NVME_PART" /mnt/nvme
fi

mkdir -p /mnt/nvme/hassio /mnt/nvme/docker /mnt/nvme/ai_models

echo "[1/4] Stopping Home Assistant Supervisor and Docker..."
systemctl stop hassio-supervisor.service || true
systemctl stop docker.service docker.socket || true

echo "[2/4] Syncing /usr/share/hassio and /var/lib/docker to NVMe..."
rsync -aHAX /usr/share/hassio/ /mnt/nvme/hassio/
rsync -aHAX /var/lib/docker/ /mnt/nvme/docker/

echo "[3/4] Mounting NVMe bind mounts..."
mount --bind /mnt/nvme/hassio /usr/share/hassio
mount --bind /mnt/nvme/docker /var/lib/docker

echo "[4/4] Starting Docker and Home Assistant Supervisor..."
systemctl start docker.service
systemctl start hassio-supervisor.service
echo "Migration complete!"
