from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "ops" / "backup" / "systemd" / "alright-mate-backup.service"
STATIC_ENV_EXAMPLE = ROOT / "ops" / "backup" / "backup.env.example"
DURABLE_RELEASE_ENV = "/etc/alright-mate/backup-release.env"
TRANSIENT_RELEASE_ENV = "/run/alright-mate-backup-release.env"
IMMUTABLE_BACKUP_IMAGE = (
    "registry.example.invalid/alright-mate/backup-tools@sha256:" + "a" * 64
)


def parse_unit() -> dict[str, dict[str, list[str]]]:
    sections: dict[str, dict[str, list[str]]] = {}
    current: dict[str, list[str]] | None = None
    for raw_line in SERVICE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = sections.setdefault(line[1:-1], {})
            continue
        if current is None or "=" not in line:
            raise AssertionError(f"invalid systemd unit line: {raw_line}")
        key, value = line.split("=", 1)
        current.setdefault(key, []).append(value)
    return sections


class BackupSystemdContractTests(unittest.TestCase):
    def test_operations_profile_renders_without_application_release_images(self) -> None:
        env = os.environ.copy()
        for name in ("API_IMAGE", "MIGRATION_IMAGE", "WEB_IMAGE"):
            env.pop(name, None)
        env["BACKUP_TOOLS_IMAGE"] = IMMUTABLE_BACKUP_IMAGE

        completed = subprocess.run(
            [
                "docker",
                "compose",
                "--project-name",
                "alright-mate",
                "-f",
                "compose.yaml",
                "-f",
                "compose.observability.yaml",
                "--profile",
                "operations",
                "config",
                "--format",
                "json",
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        config = json.loads(completed.stdout)
        self.assertEqual("alright-mate", config["name"])
        self.assertEqual(
            IMMUTABLE_BACKUP_IMAGE, config["services"]["backup"]["image"]
        )

    def test_unit_preflight_rejects_missing_mutable_or_non_lower_case_images(self) -> None:
        service = parse_unit()["Service"]
        self.assertEqual(2, len(service.get("ExecStartPre", [])))
        command = service["ExecStartPre"][0]

        # systemd turns $$ into a literal $ before passing the command to the shell.
        tokens = shlex.split(command.replace("$$", "$"), posix=True)
        git_bash = Path("C:/Program Files/Git/bin/bash.exe")
        shell = (
            str(git_bash)
            if os.name == "nt" and git_bash.is_file()
            else shutil.which("bash") or shutil.which("sh")
        )
        self.assertIsNotNone(shell, "a POSIX shell is required for the unit contract")
        tokens[0] = str(shell)

        cases = {
            "missing": None,
            "mutable tag": "registry.example.invalid/alright-mate/backup-tools:latest",
            "upper-case name": (
                "registry.example.invalid/Alright-Mate/backup-tools@sha256:" + "a" * 64
            ),
            "upper-case digest": (
                "registry.example.invalid/alright-mate/backup-tools@sha256:" + "A" * 64
            ),
        }
        for name, image in cases.items():
            with self.subTest(case=name):
                env = os.environ.copy()
                env.pop("BACKUP_TOOLS_IMAGE", None)
                if image is not None:
                    env["BACKUP_TOOLS_IMAGE"] = image
                rejected = subprocess.run(
                    tokens,
                    cwd=ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                )
                self.assertNotEqual(0, rejected.returncode, rejected.stdout)

        accepted_env = os.environ.copy()
        accepted_env["BACKUP_TOOLS_IMAGE"] = IMMUTABLE_BACKUP_IMAGE
        accepted = subprocess.run(
            tokens,
            cwd=ROOT,
            env=accepted_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        self.assertEqual(
            0,
            accepted.returncode,
            f"stdout={accepted.stdout!r}\nstderr={accepted.stderr!r}\ncommand={tokens!r}",
        )

    def test_static_secret_environment_cannot_override_release_identity(self) -> None:
        names = {
            line.split("=", 1)[0]
            for raw_line in STATIC_ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
            if (line := raw_line.strip()) and not line.startswith("#") and "=" in line
        }
        self.assertTrue(
            {"BACKUP_TOOLS_IMAGE", "RELEASE_SHA"}.isdisjoint(names),
            "release identity belongs in Ansible-owned durable or transient files",
        )

    def test_durable_identity_is_optional_and_a_transient_dropin_wins_last(self) -> None:
        service = parse_unit()["Service"]
        base_files = service.get("EnvironmentFile", [])

        self.assertEqual(
            [
                "/etc/alright-mate/backup.env",
                f"-{DURABLE_RELEASE_ENV}",
            ],
            base_files,
            "the first deployment may not have durable release identity yet",
        )

        # systemd appends EnvironmentFile directives from later drop-ins. Its
        # documented rule is that a later file wins for duplicate variables.
        merged_files = [*base_files, TRANSIENT_RELEASE_ENV]
        normalised_files = [value.removeprefix("-") for value in merged_files]
        self.assertEqual(
            [
                "/etc/alright-mate/backup.env",
                DURABLE_RELEASE_ENV,
                TRANSIENT_RELEASE_ENV,
            ],
            normalised_files,
        )
        self.assertEqual(TRANSIENT_RELEASE_ENV, normalised_files[-1])

    def test_unit_uses_existing_project_without_weakening_restrictions_or_lock(self) -> None:
        service = parse_unit()["Service"]
        self.assertEqual(
            "/etc/alright-mate/backup.env", service["EnvironmentFile"][0]
        )

        expected_restrictions = {
            "User": ["alright-mate-backup"],
            "Group": ["alright-mate-backup"],
            "SupplementaryGroups": ["docker"],
            "NoNewPrivileges": ["true"],
            "PrivateDevices": ["true"],
            "PrivateTmp": ["true"],
            "ProtectHome": ["true"],
            "ProtectSystem": ["strict"],
            "CapabilityBoundingSet": [""],
            "RestrictAddressFamilies": ["AF_UNIX"],
            "UMask": ["0077"],
        }
        for directive, expected in expected_restrictions.items():
            with self.subTest(directive=directive):
                self.assertEqual(expected, service.get(directive))

        self.assertEqual(2, len(service.get("ExecStartPre", [])))
        preflight_tokens = shlex.split(service["ExecStartPre"][1], posix=True)
        start_tokens = shlex.split(service["ExecStart"][0], posix=True)
        for tokens, expected_service in (
            (preflight_tokens, "backup-metrics-init"),
            (start_tokens, "backup"),
        ):
            with self.subTest(compose_service=expected_service):
                self.assertEqual("/usr/bin/flock", tokens[0])
                self.assertIn("--nonblock", tokens)
                self.assertIn("/run/alright-mate-backup/backup.lock", tokens)
                self.assertIn("--project-name", tokens)
                self.assertEqual(
                    "alright-mate", tokens[tokens.index("--project-name") + 1]
                )
                self.assertEqual(
                    ["compose.yaml", "compose.observability.yaml"],
                    [
                        tokens[index + 1]
                        for index, token in enumerate(tokens)
                        if token == "-f"
                    ],
                )
                self.assertNotIn("compose.production.yaml", tokens)
                self.assertIn("--env-file", tokens)
                self.assertEqual("/dev/null", tokens[tokens.index("--env-file") + 1])
                self.assertIn("--no-deps", tokens)
                self.assertIn("--pull", tokens)
                self.assertEqual("never", tokens[tokens.index("--pull") + 1])
                self.assertEqual(expected_service, tokens[-1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
