#!/usr/bin/env python3
"""Validate bounded Trivy exceptions and immutable image scan inputs."""

from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


POLICY_SECTIONS = {"vulnerabilities", "misconfigurations", "secrets", "licenses"}
DIGEST_IMAGE = re.compile(
    r"^(?:[a-z0-9]+(?:[a-z0-9.-]*[a-z0-9])?(?::[0-9]+)?/)?"
    r"[a-z0-9]+(?:[-._/][a-z0-9]+)*@sha256:[0-9a-f]{64}$"
)
VULNERABILITY_ID = re.compile(
    r"^(?:CVE-[0-9]{4}-[0-9]+|GHSA-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4})$"
)
WILDCARD_CHARACTERS = frozenset("*?[]{}")
RFC3339_UTC = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")


def parse_statement(statement: Any, context: str) -> dict[str, str]:
    if not isinstance(statement, str) or not statement.strip():
        raise ValueError(f"{context}: statement is required")
    fields: dict[str, str] = {}
    for fragment in statement.split(";"):
        key, separator, value = fragment.strip().partition("=")
        if not separator or not key or not value.strip() or key in fields:
            raise ValueError(f"{context}: statement must contain unique key=value fields")
        fields[key] = value.strip()
    for required in ("owner", "reason", "compensating_control"):
        if not fields.get(required):
            raise ValueError(f"{context}: statement requires {required}=...")
    return fields


def validate_path(value: Any, context: str) -> None:
    if not isinstance(value, str) or not value.strip() or "\\" in value:
        raise ValueError(
            f"{context}: affected paths must be non-empty repository or image-root paths"
        )
    if any(character in value for character in WILDCARD_CHARACTERS):
        raise ValueError(f"{context}: affected paths must be exact and contain no wildcards")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) in {".", ""}:
        raise ValueError(f"{context}: affected path escapes its scan root")


def validate_exception(entry: Any, category: str, index: int, today: date) -> None:
    context = f"{category}[{index}]"
    if not isinstance(entry, dict):
        raise ValueError(f"{context}: exception must be an object")
    identifier = entry.get("id")
    if not isinstance(identifier, str) or not identifier.strip():
        raise ValueError(f"{context}: id is required")
    if any(character in identifier for character in WILDCARD_CHARACTERS):
        raise ValueError(f"{context}: id must be exact and contain no wildcards")
    if category == "vulnerabilities" and VULNERABILITY_ID.fullmatch(identifier) is None:
        raise ValueError(f"{context}: vulnerability id must be an exact CVE or GHSA identifier")
    paths = entry.get("paths")
    if not isinstance(paths, list) or not paths:
        raise ValueError(f"{context}: at least one affected path is required")
    if len(paths) != len(set(paths)):
        raise ValueError(f"{context}: affected paths must be unique")
    for affected_path in paths:
        validate_path(affected_path, context)
    parse_statement(entry.get("statement"), context)
    expiry_value = entry.get("expired_at")
    if not isinstance(expiry_value, str) or RFC3339_UTC.fullmatch(expiry_value) is None:
        raise ValueError(f"{context}: expired_at must be an RFC3339 UTC timestamp")
    try:
        expiry = datetime.strptime(expiry_value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise ValueError(f"{context}: expired_at must be a valid RFC3339 UTC timestamp") from error
    lifetime_days = (expiry.date() - today).days
    if lifetime_days < 1 or lifetime_days > 30:
        raise ValueError(f"{context}: expired_at must be 1 to 30 days in the future")


def load_and_validate_policy(path: Path, today: date | None = None) -> int:
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot parse Trivy exception policy: {error}") from error
    if not isinstance(policy, dict) or set(policy) != POLICY_SECTIONS:
        raise ValueError("Trivy exception policy must contain exactly the four supported sections")
    for section in POLICY_SECTIONS:
        if not isinstance(policy[section], list):
            raise ValueError(f"{section} must be a list")
    if policy["secrets"]:
        raise ValueError("secret-scan exceptions are forbidden")
    if policy["licenses"]:
        raise ValueError("licence exceptions are not part of this scanning policy")

    validation_date = today or datetime.now(timezone.utc).date()
    exception_count = 0
    for category in ("vulnerabilities", "misconfigurations"):
        for index, entry in enumerate(policy[category]):
            validate_exception(entry, category, index, validation_date)
            exception_count += 1
    return exception_count


def validate_images(value: str) -> list[str]:
    images = value.split()
    if not images:
        raise ValueError("TRIVY_IMAGES must contain at least one digest-pinned image")
    for image in images:
        if DIGEST_IMAGE.fullmatch(image) is None:
            raise ValueError(
                "TRIVY_IMAGES entries must use name@sha256:<64 lowercase hexadecimal characters>"
            )
    return images


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ignorefile", type=Path, required=True)
    parser.add_argument("--images", required=True)
    arguments = parser.parse_args()
    try:
        exception_count = load_and_validate_policy(arguments.ignorefile)
        images = validate_images(arguments.images)
    except ValueError as error:
        parser.error(str(error))
    print(f"trivy_policy=valid exceptions={exception_count} images={len(images)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
