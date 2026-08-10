#!/usr/bin/env python3
"""Validate the exact runtime image boundary declared by Compose sources."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


COMPOSE_FILES = (
    "compose.yaml",
    "compose.production.yaml",
    "compose.observability.yaml",
    "compose.tls-bootstrap.yaml",
)
EXPECTED_KEYS = {
    "schema_version",
    "compose_files",
    "public_images",
    "authenticated_application_variables",
    "authenticated_auxiliary_variables",
    "local_only_variables",
}
APPLICATION_VARIABLES = {"API_IMAGE", "MIGRATION_IMAGE", "WEB_IMAGE"}
AUXILIARY_VARIABLES = {"BACKUP_TOOLS_IMAGE"}
LOCAL_ONLY_VARIABLES = {"IMAGE_TAG"}
IMAGE_LINE = re.compile(r"^\s*image:\s*(?P<value>[^#\r\n]+?)\s*(?:#.*)?$")
VARIABLE = re.compile(r"\$\{(?P<name>[A-Z][A-Z0-9_]*)(?::[-?][^}]*)?\}")
LOWER_DIGEST = re.compile(r"^[0-9a-f]{64}$")
IMAGE_NAME = re.compile(
    r"^[a-z0-9]+(?:[._-][a-z0-9]+)*(?::[0-9]+)?"
    r"(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*$"
)


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot parse runtime image inventory: {error}") from error
    if not isinstance(value, dict):
        raise ValueError("runtime image inventory must be an object")
    return value


def require_unique_strings(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not value or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(f"{field} must be a non-empty string list")
    if len(value) != len(set(value)):
        raise ValueError(f"{field} must not contain duplicates")
    return value


def normalise_digest_reference(reference: str) -> str:
    if reference.count("@sha256:") != 1 or any(character.isspace() for character in reference):
        raise ValueError(f"mutable or invalid image reference: {reference}")
    name_with_optional_tag, digest = reference.rsplit("@sha256:", 1)
    if LOWER_DIGEST.fullmatch(digest) is None:
        raise ValueError(f"image digest must be 64 lower-case hexadecimal characters: {reference}")
    last_slash = name_with_optional_tag.rfind("/")
    last_colon = name_with_optional_tag.rfind(":")
    name = (
        name_with_optional_tag[:last_colon]
        if last_colon > last_slash
        else name_with_optional_tag
    )
    if IMAGE_NAME.fullmatch(name) is None:
        raise ValueError(f"invalid lower-case image name: {reference}")
    return f"{name}@sha256:{digest}"


def compose_image_values(repo_root: Path, compose_files: list[str]) -> list[str]:
    values: list[str] = []
    for relative_name in compose_files:
        path = repo_root / relative_name
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as error:
            raise ValueError(f"cannot read declared Compose source {relative_name}: {error}") from error
        file_values = [
            match.group("value").strip("'\"")
            for line in lines
            if (match := IMAGE_LINE.fullmatch(line)) is not None
        ]
        if not file_values:
            raise ValueError(f"declared Compose source has no image lines: {relative_name}")
        values.extend(file_values)
    return values


def classify_compose_images(values: list[str]) -> tuple[set[str], set[str], set[str], set[str]]:
    public_images: set[str] = set()
    applications: set[str] = set()
    auxiliaries: set[str] = set()
    local_only: set[str] = set()

    for value in values:
        variables = {match.group("name") for match in VARIABLE.finditer(value)}
        if not variables:
            public_images.add(normalise_digest_reference(value))
            continue
        if variables <= APPLICATION_VARIABLES and len(variables) == 1:
            variable = next(iter(variables))
            if not value.startswith(f"${{{variable}:?") or not value.endswith("}"):
                raise ValueError(
                    f"authenticated application image variable must occupy the image value: {value}"
                )
            applications.add(variable)
            continue
        if variables == AUXILIARY_VARIABLES:
            if not value.startswith("${BACKUP_TOOLS_IMAGE:-") or not value.endswith("}"):
                raise ValueError(f"authenticated auxiliary image has an unsafe form: {value}")
            auxiliaries.add("BACKUP_TOOLS_IMAGE")
            continue
        if variables == LOCAL_ONLY_VARIABLES:
            if not re.fullmatch(
                r"alright-mate-(?:api|migration|web):\$\{IMAGE_TAG:-local\}", value
            ):
                raise ValueError(f"local-only image variable has an unsafe form: {value}")
            local_only.add("IMAGE_TAG")
            continue
        raise ValueError(f"unclassified Compose image variable in: {value}")

    return public_images, applications, auxiliaries, local_only


def validate_inventory(repo_root: Path, inventory: dict[str, Any]) -> list[str]:
    if set(inventory) != EXPECTED_KEYS:
        raise ValueError("runtime image inventory has missing or stale fields")
    if inventory["schema_version"] != 1:
        raise ValueError("runtime image inventory schema_version must be 1")

    compose_files = require_unique_strings(inventory["compose_files"], "compose_files")
    if tuple(compose_files) != COMPOSE_FILES:
        raise ValueError("compose_files must declare the exact reviewed runtime Compose sources")
    public_images = require_unique_strings(inventory["public_images"], "public_images")
    if len(public_images) != 7:
        raise ValueError("public_images must contain the seven reviewed literal runtime images")
    if public_images != sorted(public_images):
        raise ValueError("public_images must be deterministically sorted")
    for image in public_images:
        if normalise_digest_reference(image) != image:
            raise ValueError(f"public_images must use normalised name@digest references: {image}")

    applications = require_unique_strings(
        inventory["authenticated_application_variables"],
        "authenticated_application_variables",
    )
    if set(applications) != APPLICATION_VARIABLES:
        raise ValueError("authenticated application image classification is incomplete or stale")
    auxiliaries = require_unique_strings(
        inventory["authenticated_auxiliary_variables"],
        "authenticated_auxiliary_variables",
    )
    if set(auxiliaries) != AUXILIARY_VARIABLES:
        raise ValueError("authenticated auxiliary image classification is incomplete or stale")
    local_only = require_unique_strings(inventory["local_only_variables"], "local_only_variables")
    if set(local_only) != LOCAL_ONLY_VARIABLES:
        raise ValueError("local-only image classification is incomplete or stale")

    discovered = classify_compose_images(compose_image_values(repo_root, compose_files))
    expected = (
        set(public_images),
        set(applications),
        set(auxiliaries),
        set(local_only),
    )
    labels = (
        "public images",
        "authenticated application variables",
        "authenticated auxiliary variables",
        "local-only variables",
    )
    for label, actual_values, expected_values in zip(labels, discovered, expected, strict=True):
        if actual_values != expected_values:
            missing = sorted(actual_values - expected_values)
            stale = sorted(expected_values - actual_values)
            raise ValueError(f"{label} inventory drift: missing={missing} stale={stale}")
    return public_images


def validate_caller_images(value: str, expected: list[str]) -> None:
    images = value.split()
    if not images:
        raise ValueError("TRIVY_IMAGES must contain the exact public runtime image inventory")
    if len(images) != len(set(images)):
        raise ValueError("TRIVY_IMAGES must not contain duplicates")
    for image in images:
        if normalise_digest_reference(image) != image:
            raise ValueError(f"TRIVY_IMAGES must use normalised immutable references: {image}")
    if images != expected:
        missing = sorted(set(expected) - set(images))
        stale = sorted(set(images) - set(expected))
        raise ValueError(
            f"TRIVY_IMAGES must exactly match the ordered public inventory: missing={missing} stale={stale}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--images")
    parser.add_argument("--print-public-images", action="store_true")
    arguments = parser.parse_args()
    try:
        public_images = validate_inventory(arguments.repo_root, load_json(arguments.inventory))
        if arguments.images is not None:
            validate_caller_images(arguments.images, public_images)
    except ValueError as error:
        parser.error(str(error))

    if arguments.print_public_images:
        print(" ".join(public_images))
    else:
        print(
            "runtime_image_inventory=valid "
            f"public={len(public_images)} "
            f"applications={len(APPLICATION_VARIABLES)} "
            f"auxiliaries={len(AUXILIARY_VARIABLES)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
