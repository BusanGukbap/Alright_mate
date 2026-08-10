"""Regenerate required OpenTofu provider locks and detect committed lock drift."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


TOFU_ROOT = Path(__file__).resolve().parent
POLICY_PATH = TOFU_ROOT / "provider-lock-policy.json"
LOCK_PATH = TOFU_ROOT / ".terraform.lock.hcl"


def load_policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    policy = json.loads(path.read_text(encoding="utf-8"))
    if policy.get("provider") != "registry.opentofu.org/hashicorp/aws":
        raise ValueError("only the approved registry.opentofu.org/hashicorp/aws provider is allowed")
    if not re.fullmatch(r"\d+\.\d+\.\d+", str(policy.get("version", ""))):
        raise ValueError("provider lock policy requires an exact semantic version")
    platforms = policy.get("platforms")
    if platforms != ["linux_amd64", "windows_amd64"]:
        raise ValueError("provider lock policy must cover linux_amd64 and windows_amd64")
    return policy


def build_lock_command(tofu_binary: str, policy: dict[str, Any]) -> list[str]:
    return [
        tofu_binary,
        "providers",
        "lock",
        *(f"-platform={platform}" for platform in policy["platforms"]),
    ]


def run_text_command(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _provider_block(lock_text: str, provider: str) -> str:
    match = re.search(
        rf'provider "{re.escape(provider)}" \{{(?P<body>.*?)\n\}}',
        lock_text,
        re.DOTALL,
    )
    if match is None:
        raise ValueError(f"provider lock is missing {provider}")
    return match.group("body")


def _hashes(block: str) -> set[str]:
    return set(re.findall(r'"((?:h1|zh):[^"\s]+)"', block))


def verify_provider_lock(tofu_binary: str) -> None:
    policy = load_policy()
    committed_text = LOCK_PATH.read_text(encoding="utf-8")
    committed_block = _provider_block(committed_text, policy["provider"])
    if f'version     = "{policy["version"]}"' not in committed_block:
        raise ValueError("committed provider version differs from provider-lock-policy.json")

    with tempfile.TemporaryDirectory(prefix="alright-mate-tofu-lock-") as temporary_directory:
        temporary_root = Path(temporary_directory)
        for source in TOFU_ROOT.glob("*.tf"):
            shutil.copy2(source, temporary_root / source.name)
        completed = run_text_command(
            build_lock_command(tofu_binary, policy),
            temporary_root,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(f"OpenTofu provider lock regeneration failed: {detail}")
        generated_text = (temporary_root / ".terraform.lock.hcl").read_text(encoding="utf-8")

    generated_block = _provider_block(generated_text, policy["provider"])
    if f'version     = "{policy["version"]}"' not in generated_block:
        raise ValueError("regenerated provider version differs from provider-lock-policy.json")
    missing_hashes = _hashes(generated_block) - _hashes(committed_block)
    if missing_hashes:
        raise ValueError(f"committed provider lock is missing required platform hashes: {sorted(missing_hashes)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tofu", default="tofu", help="OpenTofu executable path")
    arguments = parser.parse_args()
    verify_provider_lock(arguments.tofu)
    policy = load_policy()
    print("provider lock verified for " + ", ".join(policy["platforms"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
