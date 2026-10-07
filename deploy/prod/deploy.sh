#!/usr/bin/env bash
# Usage: deploy/prod/deploy.sh <commit>, run on the laptop from the repo.
set -euo pipefail

hash=$(git rev-parse --short "${1:?usage: deploy.sh <commit>}^{commit}")
src=/srv/thenyouare/src/$hash

if ! ssh box "test -d $src"; then
	git archive "$hash" | ssh box "rm -rf $src.part && mkdir -p $src.part && tar -x -C $src.part && mv $src.part $src"
fi

ssh box bash -s -- "$hash" <<'REMOTE'
set -euo pipefail
hash=$1
cd /srv/thenyouare

docker image inspect "thenyouare:$hash" >/dev/null 2>&1 || docker build -t "thenyouare:$hash" "src/$hash"

if [ -n "$(docker ps -q --filter label=com.docker.compose.project=thenyouare --filter label=com.docker.compose.service=db)" ]; then
	# This script arrives on stdin, so the backup's docker exec must not read it.
	./backup.sh </dev/null
fi

if grep -q '^IMAGE_TAG=' .env; then
	sed -i "s/^IMAGE_TAG=.*/IMAGE_TAG=$hash/" .env
else
	printf '\nIMAGE_TAG=%s\n' "$hash" >>.env
fi
docker compose up -d

for _ in $(seq 30); do
	if curl -fsS http://127.0.0.1:8000/healthz 2>/dev/null | grep -q '"status":"ok"'; then
		echo "deployed $hash"
		exit 0
	fi
	sleep 2
done
echo "healthz did not answer ok within 60s" >&2
docker compose logs --tail 50 app >&2
exit 1
REMOTE
