"""Deterministic deployment-policy filters used by the Ansible playbooks."""

from __future__ import annotations

import ipaddress
import re


_MANAGED_COMMENT_PREFIX = "alright-mate:"
_RESERVED_RUNTIME_ENVIRONMENT_KEYS = {
    "API_IMAGE",
    "BACKUP_TOOLS_IMAGE",
    "BUILD_VERSION",
    "IMAGE_TAG",
    "MIGRATION_IMAGE",
    "RELEASE_SHA",
    "WEB_IMAGE",
}


def normalise_operator_ssh_cidrs(values: object) -> list[str]:
    """Return unique canonical host/network CIDRs or reject an unsafe input."""
    if not isinstance(values, list):
        raise ValueError("operator_ssh_cidrs must be a non-empty list of IPv4/IPv6 CIDRs")

    normalised: list[str] = []
    for raw_value in values:
        if not isinstance(raw_value, str):
            raise ValueError("each operator SSH source must be an IPv4/IPv6 CIDR string")
        value = raw_value.strip()
        if not value or value.casefold() == "any" or "/" not in value:
            raise ValueError(f"unsafe operator SSH source: {raw_value!r}")
        try:
            network = ipaddress.ip_network(value, strict=True)
        except ValueError as error:
            raise ValueError(f"invalid canonical operator SSH CIDR: {raw_value!r}") from error
        if network.prefixlen == 0:
            raise ValueError(f"world-open operator SSH CIDR is forbidden: {raw_value!r}")
        canonical = str(network)
        if canonical not in normalised:
            normalised.append(canonical)

    if not normalised:
        raise ValueError("operator_ssh_cidrs must contain at least one restricted CIDR")
    return normalised


def _managed_rule_lines(status_output: str) -> list[tuple[str, str]] | None:
    rule_lines = [
        line
        for line in status_output.splitlines()
        if re.match(r"^\s*\[\s*\d+\]", line)
    ]
    managed: list[tuple[str, str]] = []
    for line in rule_lines:
        marker_match = re.search(r"#\s*(alright-mate:[^\s]+)\s*$", line)
        if marker_match is None:
            return None
        managed.append((marker_match.group(1), line))
    return managed


def _canonical_ufw_rule_source(line: str, port: int) -> str | None:
    match = re.search(
        rf"\b{port}/tcp(?:\s+\(v6\))?\s+ALLOW IN\s+(.+?)\s+#",
        line,
    )
    if match is None:
        return None

    value = re.sub(r"\s+\(v6\)$", "", match.group(1).strip())
    if value.casefold() == "anywhere":
        return None
    try:
        if "/" in value:
            return str(ipaddress.ip_network(value, strict=False))
        address = ipaddress.ip_address(value)
        return str(ipaddress.ip_network(f"{address}/{address.max_prefixlen}"))
    except ValueError:
        return None


def ufw_policy_requires_reset(status_output: object, operator_cidrs: object) -> bool:
    """Detect UFW drift so a reset happens only when the managed rules differ."""
    cidrs = normalise_operator_ssh_cidrs(operator_cidrs)
    if not isinstance(status_output, str) or not re.search(
        r"(?mi)^Status:\s+active\s*$", status_output
    ):
        return True

    managed = _managed_rule_lines(status_output)
    if managed is None:
        return True

    expected_counts = {
        **{f"{_MANAGED_COMMENT_PREFIX}ssh:{cidr}": 1 for cidr in cidrs},
        f"{_MANAGED_COMMENT_PREFIX}http": 2,
        f"{_MANAGED_COMMENT_PREFIX}https": 2,
    }
    expected_ssh_sources = {
        f"{_MANAGED_COMMENT_PREFIX}ssh:{cidr}": cidr for cidr in cidrs
    }
    observed_counts = {marker: 0 for marker in expected_counts}
    for marker, line in managed:
        if marker not in expected_counts:
            return True
        if marker.endswith(":http") and not re.search(r"\b80/tcp(?:\s+\(v6\))?\s+ALLOW IN\b", line):
            return True
        if marker.endswith(":https") and not re.search(r"\b443/tcp(?:\s+\(v6\))?\s+ALLOW IN\b", line):
            return True
        if ":ssh:" in marker and _canonical_ufw_rule_source(
            line, 22
        ) != expected_ssh_sources[marker]:
            return True
        observed_counts[marker] += 1

    return observed_counts != expected_counts


def runtime_environment_reserved_keys(content: object) -> list[str]:
    """List release-identity keys that an injected dotenv file must not control."""
    if not isinstance(content, str):
        raise ValueError("runtime environment content must be text")

    reserved: list[str] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        key, separator, _value = line.partition("=")
        if not separator:
            continue
        key = key.strip()
        if key in _RESERVED_RUNTIME_ENVIRONMENT_KEYS and key not in reserved:
            reserved.append(key)
    return reserved


def primary_gpg_fingerprints(colon_output: object) -> list[str]:
    """Return fingerprints structurally attached to primary public-key records."""
    if not isinstance(colon_output, str):
        raise ValueError("GPG colon output must be text")

    fingerprints: list[str] = []
    awaiting_primary_fingerprint = False
    for raw_line in colon_output.splitlines():
        fields = raw_line.split(":")
        record_type = fields[0] if fields else ""
        if record_type == "pub":
            awaiting_primary_fingerprint = True
            continue
        if record_type in {"sub", "sec", "ssb"}:
            awaiting_primary_fingerprint = False
            continue
        if record_type != "fpr" or not awaiting_primary_fingerprint:
            continue
        if len(fields) <= 9 or not re.fullmatch(r"[0-9A-Fa-f]{40}", fields[9]):
            raise ValueError("primary GPG fingerprint record is malformed")
        fingerprints.append(fields[9].upper())
        awaiting_primary_fingerprint = False
    return fingerprints


def release_image_revision_mismatches(
    inspection_register: object, release_sha: object
) -> list[str]:
    """List pulled image references whose OCI revision is not the release SHA."""
    if not isinstance(release_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", release_sha):
        raise ValueError("release SHA must be exactly 40 lower-case hexadecimal characters")
    if not isinstance(inspection_register, dict) or not isinstance(
        inspection_register.get("results"), list
    ):
        raise ValueError("registered image inspections must contain a results list")

    mismatches: list[str] = []
    for index, result in enumerate(inspection_register["results"]):
        reference = (
            result.get("item")
            if isinstance(result, dict) and isinstance(result.get("item"), str)
            else f"inspection[{index}]"
        )
        images = result.get("images") if isinstance(result, dict) else None
        revision = None
        if isinstance(images, list) and len(images) == 1 and isinstance(images[0], dict):
            config = images[0].get("Config")
            labels = config.get("Labels") if isinstance(config, dict) else None
            if isinstance(labels, dict):
                revision = labels.get("org.opencontainers.image.revision")
        if revision != release_sha:
            mismatches.append(reference)
    return mismatches


class FilterModule:
    """Expose the policy helpers to Jinja."""

    def filters(self) -> dict[str, object]:
        return {
            "normalise_operator_ssh_cidrs": normalise_operator_ssh_cidrs,
            "primary_gpg_fingerprints": primary_gpg_fingerprints,
            "release_image_revision_mismatches": release_image_revision_mismatches,
            "runtime_environment_reserved_keys": runtime_environment_reserved_keys,
            "ufw_policy_requires_reset": ufw_policy_requires_reset,
        }
