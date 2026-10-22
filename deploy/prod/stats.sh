#!/usr/bin/env bash
# Usage: deploy/prod/stats.sh [days], run on the laptop. Prints the last 30 days by default.
set -euo pipefail

ssh box "docker compose -f /srv/thenyouare/compose.yaml exec -T db psql -q -U oddstage oddstage -v days=${1:-30}" \
	<"$(dirname "$0")/stats.sql"
