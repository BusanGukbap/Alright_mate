#!/usr/bin/env python3
"""Validate complete, explicit Trivy dependency-manifest scan coverage."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any


IGNORED_DIRECTORIES = {
    ".Codex",
    ".git",
    ".terraform",
    ".tools",
    "__pycache__",
    "dist",
    "node_modules",
    "playwright-report",
    "test-results",
}
EXACT_MANIFESTS = {
    "Pipfile": "python",
    "Pipfile.lock": "python",
    "go.mod": "go",
    "go.sum": "go",
    "npm-shrinkwrap.json": "npm",
    "package-lock.json": "npm",
    "package.json": "npm",
    "poetry.lock": "python",
    "pyproject.toml": "python",
    "uv.lock": "python",
}
SAFE_REPOSITORY_PATH = re.compile(r"^[A-Za-z0-9._/-]+$")
REQUIRED_ROOT_FIELDS = {
    "path",
    "ecosystem",
    "manifests",
    "dependency_scope",
    "trivy_dev_dependencies",
    "rationale",
}


def manifest_ecosystem(filename: str) -> str | None:
    ecosystem = EXACT_MANIFESTS.get(filename)
    if ecosystem is not None:
        return ecosystem
    if filename.startswith("requirements") and filename.endswith(".txt"):
        return "python"
    return None


def normalise_repository_path(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"{context}: expected a non-empty POSIX repository path")
    if SAFE_REPOSITORY_PATH.fullmatch(value) is None:
        raise ValueError(f"{context}: path contains unsupported characters")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) in {"", "."}:
        raise ValueError(f"{context}: path escapes the repository")
    if str(path) != value:
        raise ValueError(f"{context}: path must be normalised")
    return value


def discover_manifests(repo_root: Path) -> set[str]:
    discovered: set[str] = set()
    for directory, child_directories, filenames in os.walk(repo_root):
        child_directories[:] = [
            child for child in child_directories if child not in IGNORED_DIRECTORIES
        ]
        relative_directory = Path(directory).relative_to(repo_root)
        for filename in filenames:
            if manifest_ecosystem(filename) is not None:
                discovered.add((relative_directory / filename).as_posix())
    return discovered


def load_scan_roots(repo_root: Path, coverage_path: Path) -> list[str]:
    try:
        coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot parse dependency coverage: {error}") from error
    if not isinstance(coverage, dict) or set(coverage) != {"version", "roots"}:
        raise ValueError("dependency coverage must contain exactly version and roots")
    if coverage["version"] != 1:
        raise ValueError("dependency coverage version must be 1")
    roots = coverage["roots"]
    if not isinstance(roots, list) or not roots:
        raise ValueError("dependency coverage roots must be a non-empty list")

    declared_manifests: set[str] = set()
    declared_roots: set[str] = set()
    ordered_roots: list[str] = []
    for index, entry in enumerate(roots):
        context = f"roots[{index}]"
        if not isinstance(entry, dict) or set(entry) != REQUIRED_ROOT_FIELDS:
            raise ValueError(f"{context}: fields must be exactly {sorted(REQUIRED_ROOT_FIELDS)}")
        root = normalise_repository_path(entry["path"], f"{context}.path")
        if root in declared_roots:
            raise ValueError(f"{context}: duplicate scan root {root}")
        root_path = repo_root / Path(root)
        if not root_path.is_dir():
            raise ValueError(f"{context}: scan root does not exist: {root}")
        declared_roots.add(root)
        ordered_roots.append(root)

        ecosystem = entry["ecosystem"]
        if ecosystem not in {"go", "npm", "python"}:
            raise ValueError(f"{context}: unsupported ecosystem {ecosystem!r}")
        if entry["dependency_scope"] not in {"runtime", "operations", "development"}:
            raise ValueError(f"{context}: invalid dependency_scope")
        development_policy = entry["trivy_dev_dependencies"]
        if development_policy not in {"not-applicable", "suppressed"}:
            raise ValueError(f"{context}: invalid trivy_dev_dependencies")
        if development_policy == "suppressed" and ecosystem != "npm":
            raise ValueError(f"{context}: development suppression applies only to npm")
        if not isinstance(entry["rationale"], str) or not entry["rationale"].strip():
            raise ValueError(f"{context}: rationale is required")

        manifests = entry["manifests"]
        if not isinstance(manifests, list) or not manifests:
            raise ValueError(f"{context}: manifests must be a non-empty list")
        for manifest_index, value in enumerate(manifests):
            manifest_context = f"{context}.manifests[{manifest_index}]"
            manifest = normalise_repository_path(value, manifest_context)
            if manifest != root and not manifest.startswith(f"{root}/"):
                raise ValueError(f"{manifest_context}: manifest escapes scan root {root}")
            if manifest in declared_manifests:
                raise ValueError(f"{manifest_context}: duplicate manifest {manifest}")
            manifest_path = repo_root / Path(manifest)
            if not manifest_path.is_file():
                raise ValueError(f"{manifest_context}: manifest does not exist: {manifest}")
            actual_ecosystem = manifest_ecosystem(manifest_path.name)
            if actual_ecosystem != ecosystem:
                raise ValueError(
                    f"{manifest_context}: {manifest} belongs to {actual_ecosystem}, not {ecosystem}"
                )
            declared_manifests.add(manifest)

    discovered_manifests = discover_manifests(repo_root)
    missing = sorted(discovered_manifests - declared_manifests)
    stale = sorted(declared_manifests - discovered_manifests)
    if missing or stale:
        details: list[str] = []
        if missing:
            details.append("missing scan coverage: " + ", ".join(missing))
        if stale:
            details.append("declared non-manifests: " + ", ".join(stale))
        raise ValueError("; ".join(details))
    return ordered_roots


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--coverage", type=Path, required=True)
    parser.add_argument("--print-roots", action="store_true")
    arguments = parser.parse_args()
    try:
        roots = load_scan_roots(arguments.repo_root.resolve(), arguments.coverage)
    except ValueError as error:
        parser.error(str(error))
    if arguments.print_roots:
        print("\n".join(roots))
    else:
        print(f"dependency_coverage=valid roots={len(roots)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
