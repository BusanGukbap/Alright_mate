#!/usr/bin/env bash
set -euo pipefail

release_sha=${1:-}
: "${GHCR_REPOSITORY_PREFIX:?GHCR_REPOSITORY_PREFIX is required}"
python_bin=${PYTHON_BIN:-python3}

if [[ ! "$release_sha" =~ ^[0-9a-f]{40}$ ]]; then
  echo "release SHA must contain exactly 40 lower-case hexadecimal characters" >&2
  exit 64
fi
if [[ ! "$GHCR_REPOSITORY_PREFIX" =~ ^ghcr\.io/[a-z0-9]+([._-][a-z0-9]+)*(/[a-z0-9]+([._-][a-z0-9]+)*)+$ ]]; then
  echo "GHCR_REPOSITORY_PREFIX must be a lower-case ghcr.io repository path" >&2
  exit 64
fi

temporary_parent=${RUNNER_TEMP:-${TMPDIR:-/tmp}}
marker_file=$(mktemp "${temporary_parent}/alright-mate-eligibility.XXXXXX")
cleanup() {
  rm -f -- "$marker_file"
}
trap cleanup EXIT INT TERM

marker_reference="${GHCR_REPOSITORY_PREFIX}-release:${release_sha}"
if ! docker buildx imagetools inspect --raw "$marker_reference" >"$marker_file"; then
  echo "release has no scanned eligibility manifest" >&2
  exit 69
fi

resolved=$("$python_bin" - "$marker_file" "$GHCR_REPOSITORY_PREFIX" "$release_sha" <<'PY'
from __future__ import annotations

import json
from pathlib import Path
import re
import sys


marker_path = Path(sys.argv[1])
prefix = sys.argv[2]
release_sha = sys.argv[3]
try:
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
except (OSError, UnicodeError, json.JSONDecodeError) as error:
    raise SystemExit(f"release eligibility manifest is invalid: {error}")

annotations = marker.get("annotations")
if not isinstance(annotations, dict):
    raise SystemExit("release eligibility manifest has no root annotations")
if annotations.get("io.alright-mate.release.sha") != release_sha:
    raise SystemExit("release eligibility manifest is bound to another commit")

reference_pattern = re.compile(r"^ghcr\.io/[a-z0-9]+(?:[._/-][a-z0-9]+)*@sha256:[0-9a-f]{64}$")
for kind in ("api", "migration", "web", "backup"):
    key = f"io.alright-mate.image.{kind}"
    reference = annotations.get(key)
    expected_prefix = f"{prefix}-{kind}@sha256:"
    if not isinstance(reference, str) or not reference_pattern.fullmatch(reference):
        raise SystemExit(f"release eligibility {kind} reference is invalid")
    if not reference.startswith(expected_prefix):
        raise SystemExit(f"release eligibility {kind} reference uses another repository")
    print(f"{kind}_image_reference={reference}")

rollback_safe = annotations.get("io.alright-mate.migration.rollback-safe")
if rollback_safe not in {"true", "false"}:
    raise SystemExit("release eligibility has no migration rollback safety declaration")
print(f"migration_rollback_safe={rollback_safe}")
PY
)

for key in api_image_reference migration_image_reference web_image_reference backup_image_reference; do
  reference=$(printf '%s\n' "$resolved" | awk -F= -v wanted="$key" '$1 == wanted { print substr($0, index($0, "=") + 1); exit }')
  if ! docker buildx imagetools inspect --raw "$reference" >/dev/null; then
    echo "eligible release image is unavailable for ${key}" >&2
    exit 69
  fi
done

printf '%s\n' "$resolved"
