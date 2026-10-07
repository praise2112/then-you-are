#!/usr/bin/env bash
set -euo pipefail

set -a
# shellcheck source=/dev/null
source /srv/thenyouare/backup.env
set +a

restic backup --stdin-filename oddstage.sql --stdin-from-command -- \
	docker compose -f /srv/thenyouare/compose.yaml exec -T db pg_dump -U oddstage oddstage
restic forget --keep-daily 14 --prune --max-unused 0
