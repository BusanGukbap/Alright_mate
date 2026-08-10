#!/usr/bin/env bash
set -euo pipefail

script_directory=$(CDPATH='' cd -- "$(dirname "$0")" && pwd)
repository_root=$(CDPATH='' cd -- "${script_directory}/../.." && pwd)
declaration_file=${1:-${script_directory}/migration-compatibility.env}
python_bin=${PYTHON_BIN:-python3}

"$python_bin" - "$repository_root" "$declaration_file" <<'PY'
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import sys


repository_root = Path(sys.argv[1])
declaration_path = Path(sys.argv[2])
migration_root = repository_root / "apps" / "api" / "db" / "migrations"

declaration: dict[str, str] = {}
for line in declaration_path.read_text(encoding="utf-8").splitlines():
    if not line or line.startswith("#"):
        continue
    if "=" not in line:
        raise SystemExit("migration compatibility declaration contains an invalid line")
    key, value = line.split("=", 1)
    if key in declaration:
        raise SystemExit(f"migration compatibility declaration repeats {key}")
    declaration[key] = value

if set(declaration) != {"schema", "migration_tree_sha256", "rollback_safe"}:
    raise SystemExit("migration compatibility declaration must contain exactly the reviewed keys")
if declaration["schema"] != "1":
    raise SystemExit("migration compatibility declaration schema is unsupported")
if declaration["rollback_safe"] not in {"true", "false"}:
    raise SystemExit("rollback_safe must be true or false")
if not re.fullmatch(r"[0-9a-f]{64}", declaration["migration_tree_sha256"]):
    raise SystemExit("migration_tree_sha256 must be a lower-case SHA-256 digest")

migrations = sorted(migration_root.rglob("*.sql"))
if not migrations:
    raise SystemExit("at least one SQL migration is required")
digest = hashlib.sha256()
for migration in migrations:
    digest.update(migration.relative_to(migration_root).as_posix().encode("utf-8"))
    digest.update(b"\0")
    digest.update(migration.read_bytes())
    digest.update(b"\0")

actual = digest.hexdigest()
if actual != declaration["migration_tree_sha256"]:
    raise SystemExit(
        "migration files changed without a reviewed rollback compatibility declaration update"
    )

print(f"migration_rollback_safe={declaration['rollback_safe']}")
print(f"migration_tree_sha256={actual}")
PY
