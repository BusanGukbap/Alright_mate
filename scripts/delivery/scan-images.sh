#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

: "${GHCR_USERNAME:?GHCR_USERNAME is required}"
: "${GHCR_TOKEN:?GHCR_TOKEN is required}"
: "${TRIVY_IMAGES:?TRIVY_IMAGES is required}"

script_directory=$(CDPATH='' cd -- "$(dirname "$0")" && pwd)
repository_root=$(CDPATH='' cd -- "${script_directory}/../.." && pwd)
temporary_parent=${RUNNER_TEMP:-${TMPDIR:-/tmp}}
scan_workdir=$(mktemp -d "${temporary_parent}/alright-mate-image-scan.XXXXXX")
docker_config_directory="${scan_workdir}/docker"
trivy_image='aquasec/trivy:0.73.0@sha256:7cced7cae583819fc7806d4cbc0dbbc7cad18b99f7d3e235192e6da8c091045c'
cache_volume=${TRIVY_CACHE_VOLUME:-alright-mate-trivy-cache}
registry_login_active=0
python_bin=${PYTHON_BIN:-python3}

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  set +e
  if (( registry_login_active == 1 )); then
    DOCKER_CONFIG="$docker_config_directory" docker logout ghcr.io >/dev/null 2>&1
  fi
  case "$scan_workdir" in
    "${temporary_parent}"/alright-mate-image-scan.*)
      if [[ -d "$scan_workdir" && ! -L "$scan_workdir" ]]; then
        rm -rf -- "$scan_workdir"
      fi
      ;;
  esac
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

mkdir "$docker_config_directory"
chmod 0700 "$scan_workdir" "$docker_config_directory"

"$python_bin" "${repository_root}/ops/security/validate_trivy_policy.py" \
  --ignorefile "${repository_root}/ops/security/.trivyignore.yaml" \
  --images "$TRIVY_IMAGES"

printf '%s' "$GHCR_TOKEN" | DOCKER_CONFIG="$docker_config_directory" \
  docker login ghcr.io --username "$GHCR_USERNAME" --password-stdin >/dev/null
registry_login_active=1
[[ -f "${docker_config_directory}/config.json" ]] || {
  echo "isolated GHCR credential file was not created" >&2
  exit 70
}
chmod 0600 "${docker_config_directory}/config.json"

for image in $TRIVY_IMAGES; do
  docker run --rm \
    --read-only \
    --security-opt no-new-privileges:true \
    --cap-drop ALL \
    --tmpfs /tmp:size=512m,mode=1777 \
    --volume "${cache_volume}:/root/.cache/" \
    --volume "${docker_config_directory}/config.json:/root/.docker/config.json:ro" \
    --volume "${repository_root}:/workspace:ro" \
    --workdir /workspace \
    "$trivy_image" \
    --config /workspace/ops/security/trivy.yaml image "$image"
done
