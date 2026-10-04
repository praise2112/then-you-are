#!/usr/bin/env bash
# Build, deploy, remove and inspect the House model service on Cloud Run.
# Usage: house.sh build|up|down|status|logs
set -euo pipefail

PROJECT=PROJECT
REGION=europe-west1
BILLING_ACCOUNT=***REMOVED***
IMAGE=$REGION-docker.pkg.dev/$PROJECT/oddstage/house
GGUF=${HOUSE_GGUF:-$HOME/oddstage-runs/prefs-qwen35-08b-ipo-sft-r2-s0/model-q4_k_m.gguf}
HERE=$(cd "$(dirname "$0")" && pwd)

gc() { gcloud --project "$PROJECT" --quiet "$@"; }

billing_enabled() {
  [ "$(gc billing projects describe "$PROJECT" --format='value(billingEnabled)')" = True ]
}

build() {
  local ctx
  ctx=$(mktemp -d)
  cp "$HERE/Dockerfile" "$ctx/"
  cp "$GGUF" "$ctx/house.gguf"
  gc builds submit "$ctx" --tag "$IMAGE"
  rm -rf "$ctx"
  gc storage rm "gs://${PROJECT}_cloudbuild/source/**"
}

up() {
  if ! billing_enabled; then
    gc billing projects link "$PROJECT" --billing-account "$BILLING_ACCOUNT"
    # A service that lived through a billing stop never gets an instance again.
    gc run services delete house --region "$REGION" 2>/dev/null || true
  fi
  local digest
  digest=$(gc artifacts docker images describe "$IMAGE:latest" --format='value(image_summary.digest)')
  sed "s|image: IMAGE|image: $IMAGE@$digest|" "$HERE/service.yaml" \
    | gc run services replace - --region "$REGION"
  gc run services add-iam-policy-binding house --region "$REGION" \
    --member=allUsers --role=roles/run.invoker --format=none
  status
}

down() {
  gc run services delete house --region "$REGION"
}

status() {
  echo "billing enabled: $(billing_enabled && echo yes || echo no)"
  gc run services describe house --region "$REGION" \
    --format='value(status.url,status.latestReadyRevisionName)' 2>/dev/null \
    || echo "service: not deployed"
}

logs() {
  gc logging read \
    "resource.type=cloud_run_revision AND resource.labels.service_name=house" \
    --limit 50 --format='table(timestamp,httpRequest.status,httpRequest.latency,textPayload)'
}

case "${1:-}" in
  build | up | down | status | logs) "$1" ;;
  *) echo "usage: $0 build|up|down|status|logs" >&2; exit 2 ;;
esac
