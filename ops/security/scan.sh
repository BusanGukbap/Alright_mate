#!/bin/sh
set -eu

repo_root=$(CDPATH='' cd -- "$(dirname "$0")/../.." && pwd)
trivy_image='aquasec/trivy:0.73.0@sha256:7cced7cae583819fc7806d4cbc0dbbc7cad18b99f7d3e235192e6da8c091045c'
cache_volume=${TRIVY_CACHE_VOLUME:-alright-mate-trivy-cache}
images=${TRIVY_IMAGES:-}
coverage_file="${repo_root}/ops/security/dependency-scan-roots.json"
runtime_inventory_file="${repo_root}/ops/security/runtime-image-inventory.json"
scan_run_id=${TRIVY_SCAN_RUN_ID:-"alright-mate-security-$$-$(date +%s)"}

case "$scan_run_id" in
  *[!A-Za-z0-9_.-]* | '')
    echo "TRIVY_SCAN_RUN_ID contains unsupported characters" >&2
    exit 2
    ;;
esac

command -v docker >/dev/null 2>&1 || {
  echo "docker is required" >&2
  exit 2
}
command -v timeout >/dev/null 2>&1 || {
  echo "GNU timeout is required" >&2
  exit 2
}

python3 "${repo_root}/ops/security/validate_runtime_image_inventory.py" \
  --repo-root "$repo_root" \
  --inventory "$runtime_inventory_file" \
  --images "$images"

python3 "${repo_root}/ops/security/validate_trivy_policy.py" \
  --ignorefile "${repo_root}/ops/security/.trivyignore.yaml" \
  --images "$images"

manifest_roots=$(
  python3 "${repo_root}/ops/security/validate_dependency_coverage.py" \
    --repo-root "$repo_root" \
    --coverage "$coverage_file" \
    --print-roots
)

[ -n "$manifest_roots" ] || {
  echo "dependency coverage produced no vulnerability scan roots" >&2
  exit 2
}

cleanup_scan_containers() {
  cleanup_failed=0
  container_ids=$(docker ps -aq \
    --filter "label=org.alright-mate.security-scan=${scan_run_id}" 2>/dev/null) || return 1
  for container_id in $container_ids; do
    case "$container_id" in
      *[!0-9a-f]* | '')
        echo "refusing to remove unexpected Trivy container identifier" >&2
        cleanup_failed=1
        continue
        ;;
    esac
    actual_run_id=$(docker inspect --format \
      '{{ index .Config.Labels "org.alright-mate.security-scan" }}' \
      "$container_id" 2>/dev/null) || {
        cleanup_failed=1
        continue
      }
    if [ "$actual_run_id" != "$scan_run_id" ]; then
      echo "refusing to remove a Trivy container from another scan" >&2
      cleanup_failed=1
      continue
    fi
    docker rm -f "$container_id" >/dev/null || cleanup_failed=1
  done
  residual=$(docker ps -aq \
    --filter "label=org.alright-mate.security-scan=${scan_run_id}" 2>/dev/null) || return 1
  if [ -n "$residual" ]; then
    echo "Trivy scan cleanup left a labelled container" >&2
    cleanup_failed=1
  fi
  [ "$cleanup_failed" -eq 0 ]
}

if [ "${TRIVY_BOUNDED_CHILD:-0}" != 1 ]; then
  set +e
  timeout --signal=INT --kill-after=10s 600s \
    env TRIVY_BOUNDED_CHILD=1 TRIVY_SCAN_RUN_ID="$scan_run_id" "$0"
  scan_status=$?
  set -e
  if ! cleanup_scan_containers; then
    scan_status=1
  fi
  exit "$scan_status"
fi

stage_index=0

cleanup_on_exit() {
  scan_status=$?
  trap - EXIT HUP INT TERM
  if ! cleanup_scan_containers; then
    scan_status=1
  fi
  exit "$scan_status"
}

trap cleanup_on_exit EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

run_trivy_online() {
  tmpfs_size=$1
  shift
  stage_index=$((stage_index + 1))
  container_name="${scan_run_id}-${stage_index}"
  timeout --signal=INT --kill-after=10s 120s docker run --rm \
    --name "$container_name" \
    --label "org.alright-mate.security-scan=${scan_run_id}" \
    --read-only \
    --security-opt no-new-privileges:true \
    --cap-drop ALL \
    --tmpfs "/tmp:size=${tmpfs_size},mode=1777" \
    --volume "${cache_volume}:/root/.cache/" \
    --volume "${repo_root}:/workspace:ro" \
    --workdir /workspace \
    "$trivy_image" \
    --config /workspace/ops/security/trivy.yaml \
    --timeout 2m "$@"
}

run_trivy_offline() {
  stage_index=$((stage_index + 1))
  container_name="${scan_run_id}-${stage_index}"
  timeout --signal=INT --kill-after=10s 120s docker run --rm \
    --name "$container_name" \
    --label "org.alright-mate.security-scan=${scan_run_id}" \
    --network none \
    --read-only \
    --security-opt no-new-privileges:true \
    --cap-drop ALL \
    --tmpfs /tmp:size=512m,mode=1777 \
    --volume "${cache_volume}:/root/.cache/" \
    --volume "${repo_root}:/workspace:ro" \
    --workdir /workspace \
    "$trivy_image" \
    --config /workspace/ops/security/trivy.yaml \
    --timeout 2m "$@"
}

# Fetch the vulnerability database once, then keep dependency and secret
# analysis offline and bounded independently for each declared manifest root.
run_trivy_online 512m fs --download-db-only

for manifest_root in $manifest_roots; do
  run_trivy_offline fs --scanners vuln --skip-db-update --offline-scan \
    "/workspace/${manifest_root}"
done

run_trivy_offline fs --scanners secret --skip-db-update --offline-scan /workspace
run_trivy_online 512m config /workspace/infra
run_trivy_online 512m config /workspace/ops
run_trivy_online 512m config /workspace/apps/api/Dockerfile
run_trivy_online 512m config /workspace/apps/web/Dockerfile

for image in $images; do
  run_trivy_online 1g image --skip-db-update "$image"
done
