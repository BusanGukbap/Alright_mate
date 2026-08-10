from __future__ import annotations

import base64
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
import json

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
SCRIPTS = ROOT / "scripts" / "delivery"
SHA = "0123456789abcdef0123456789abcdef01234567"
PREVIOUS_SHA = "89abcdef0123456789abcdef0123456789abcdef"
OLDER_SHA = "fedcba9876543210fedcba9876543210fedcba98"
OPERATION_ID = "d" * 32
ROLLBACK_OPERATION_ID = "e" * 32
MANUAL_ROLLBACK_OPERATION_ID = "f" * 32
FAILED_OPERATION_ID = "9" * 32
UNSAFE_OPERATION_ID = "a" * 32
API_REFERENCE = f"ghcr.io/example/alright-mate-api@sha256:{'a' * 64}"
MIGRATION_REFERENCE = f"ghcr.io/example/alright-mate-migration@sha256:{'b' * 64}"
WEB_REFERENCE = f"ghcr.io/example/alright-mate-web@sha256:{'c' * 64}"
BACKUP_REFERENCE = f"ghcr.io/example/alright-mate-backup@sha256:{'0' * 64}"
PREVIOUS_API_REFERENCE = f"ghcr.io/example/alright-mate-api@sha256:{'1' * 64}"
PREVIOUS_MIGRATION_REFERENCE = f"ghcr.io/example/alright-mate-migration@sha256:{'2' * 64}"
PREVIOUS_WEB_REFERENCE = f"ghcr.io/example/alright-mate-web@sha256:{'3' * 64}"
PREVIOUS_BACKUP_REFERENCE = f"ghcr.io/example/alright-mate-backup@sha256:{'7' * 64}"
ACTION_PIN = re.compile(
    r"^\s*uses:\s+[^\s@]+@(?P<sha>[0-9a-f]{40})\s+#\s+v\d[^\s]*\s*$",
    re.MULTILINE,
)


def read(relative: str) -> str:
    path = ROOT / relative
    if not path.is_file():
        raise AssertionError(f"required delivery asset is missing: {relative}")
    return path.read_text(encoding="utf-8")


def load_workflow(name: str) -> dict:
    loaded = yaml.safe_load(read(f".github/workflows/{name}"))
    if not isinstance(loaded, dict):
        raise AssertionError(f"workflow is not a YAML mapping: {name}")
    return loaded


def bash_path(path: Path) -> str:
    resolved = path.resolve()
    drive, tail = os.path.splitdrive(str(resolved))
    if drive:
        return f"/{drive[0].lower()}{tail.replace(os.sep, '/')}"
    return resolved.as_posix()


def find_bash() -> str:
    candidates = [
        r"C:\Program Files\Git\bin\bash.exe",
        "/bin/bash",
        shutil.which("bash"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise unittest.SkipTest("Bash is unavailable")


def temporary_parent() -> Path | None:
    if os.name != "nt":
        return None
    parent = ROOT / "tests" / "delivery" / ".scratch"
    parent.mkdir(parents=True, exist_ok=True)
    return parent


class WorkflowPolicyTests(unittest.TestCase):
    def test_required_workflows_and_delivery_entry_points_exist(self) -> None:
        for relative in (
            ".github/workflows/ci.yml",
            ".github/workflows/deploy.yml",
            ".github/workflows/rollback.yml",
            "scripts/delivery/release.sh",
            "scripts/delivery/resolve-images.sh",
            "scripts/delivery/remote-release.sh",
            "scripts/delivery/scan-images.sh",
            "scripts/delivery/migration-compatibility.sh",
            "scripts/delivery/migration-compatibility.env",
            "Makefile",
        ):
            self.assertTrue((ROOT / relative).is_file(), relative)

    def test_every_third_party_action_has_a_full_sha_and_version_comment(self) -> None:
        workflow_files = sorted(WORKFLOWS.glob("*.yml"))
        self.assertEqual([path.name for path in workflow_files], ["ci.yml", "deploy.yml", "rollback.yml"])
        for path in workflow_files:
            source = path.read_text(encoding="utf-8")
            uses_lines = [line for line in source.splitlines() if re.match(r"^\s*uses:", line)]
            self.assertGreater(len(uses_lines), 0, path.name)
            for line in uses_lines:
                self.assertRegex(line, ACTION_PIN, f"unpinned action in {path.name}: {line}")

    def test_ci_runs_every_required_gate_on_pull_requests_and_main(self) -> None:
        workflow = load_workflow("ci.yml")
        self.assertIn("pull_request", workflow["on"])
        self.assertEqual(workflow["on"]["push"]["branches"], ["main"])
        self.assertTrue(workflow["concurrency"]["cancel-in-progress"])
        self.assertEqual(workflow["permissions"], {"contents": "read"})

        commands = "\n".join(
            str(step.get("run", ""))
            for job in workflow["jobs"].values()
            for step in job.get("steps", [])
        )
        required_commands = (
            "redocly lint",
            "go generate ./...",
            "REQUIRE_DOCKER=1 go test -mod=readonly ./... -count=1",
            "npm run generate",
            "npm run test -- --run",
            "npm run typecheck",
            "npm run lint",
            "npm run spellcheck",
            "npm run build",
            "runtime-config.test.mjs",
            "docker-runtime.test.mjs",
            "npm test",
            "infra.tests.test_infra_contract",
            "infra.tests.test_resolved_production_compose",
            "tofu fmt -check",
            "ansible-playbook --syntax-check",
            "ansible-lint",
            "unittest discover -s tests/ops",
            "ops/security/scan.sh",
            "TRIVY_SCAN_RUN_ID",
            "scripts/delivery/scan-images.sh",
        )
        for command in required_commands:
            self.assertIn(command, commands)

        for name, job in workflow["jobs"].items():
            self.assertIsInstance(job.get("timeout-minutes"), int, name)
            self.assertLessEqual(job["timeout-minutes"], 45, name)

    def test_ci_and_local_lint_cover_current_go_and_every_delivery_shell(self) -> None:
        workflow = load_workflow("ci.yml")
        dockerfile = read("apps/api/Dockerfile")
        makefile = read("Makefile")

        builder_match = re.search(r"(?m)^FROM golang:(?P<version>\d+\.\d+\.\d+)-", dockerfile)
        self.assertIsNotNone(builder_match)
        builder_version = builder_match.group("version")

        setup_go_steps = [
            step
            for job in workflow["jobs"].values()
            for step in job.get("steps", [])
            if str(step.get("uses", "")).startswith("actions/setup-go@")
        ]
        self.assertGreater(len(setup_go_steps), 0)
        self.assertEqual(
            {str(step.get("with", {}).get("go-version", "")) for step in setup_go_steps},
            {builder_version},
        )

        ci_commands = "\n".join(
            str(step.get("run", ""))
            for job in workflow["jobs"].values()
            for step in job.get("steps", [])
        )
        shell_paths = sorted(
            path.relative_to(ROOT).as_posix()
            for path in SCRIPTS.glob("*.sh")
        )
        self.assertGreater(len(shell_paths), 0)
        for path in shell_paths:
            self.assertIn(f"/workspace/{path}", ci_commands)
            self.assertIn(f"/workspace/{path}", makefile)

    def test_operations_and_publish_scans_receive_their_required_immutable_images(self) -> None:
        workflow = load_workflow("ci.yml")
        operations = workflow["jobs"]["operations"]
        operations_scan = next(
            step
            for step in operations["steps"]
            if "ops/security/scan.sh" in str(step.get("run", ""))
        )
        self.assertIn("validate_runtime_image_inventory.py", operations_scan["run"])
        self.assertIn("runtime-image-inventory.json", operations_scan["run"])
        self.assertIn("--print-public-images", operations_scan["run"])
        self.assertIn('TRIVY_IMAGES="$(', operations_scan["run"])
        self.assertIn("TRIVY_SCAN_RUN_ID", operations_scan["run"])
        self.assertIn("TRIVY_CACHE_VOLUME", operations_scan["run"])

        inventory_path = ROOT / "ops" / "security" / "runtime-image-inventory.json"
        validator_path = ROOT / "ops" / "security" / "validate_runtime_image_inventory.py"
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        printed = subprocess.run(
            [
                sys.executable,
                str(validator_path),
                "--repo-root",
                str(ROOT),
                "--inventory",
                str(inventory_path),
                "--print-public-images",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        self.assertEqual(printed.returncode, 0, printed.stderr)
        public_images = printed.stdout.strip().split()
        self.assertEqual(public_images, inventory["public_images"])
        self.assertGreater(len(public_images), 0)
        for reference in public_images:
            self.assertRegex(
                reference,
                r"^[a-z0-9]+(?:[._/-][a-z0-9]+)*@sha256:[0-9a-f]{64}$",
            )

        publish_steps = workflow["jobs"]["publish"]["steps"]
        private_scan_index = next(
            index
            for index, step in enumerate(publish_steps)
            if step.get("run") == "bash scripts/delivery/scan-images.sh"
        )
        eligibility_index = next(
            index
            for index, step in enumerate(publish_steps)
            if step.get("name") == "Publish the scanned release eligibility manifest"
        )
        self.assertLess(private_scan_index, eligibility_index)
        self.assertEqual(
            publish_steps[private_scan_index]["env"]["TRIVY_IMAGES"],
            "${{ steps.registry.outputs.prefix }}-api@${{ steps.api.outputs.digest }} "
            "${{ steps.registry.outputs.prefix }}-migration@${{ steps.migration.outputs.digest }} "
            "${{ steps.registry.outputs.prefix }}-web@${{ steps.web.outputs.digest }} "
            "${{ steps.registry.outputs.prefix }}-backup@${{ steps.backup.outputs.digest }}",
        )

    def test_ci_discovers_every_operations_test_module(self) -> None:
        workflow = load_workflow("ci.yml")
        operations_test = next(
            step
            for step in workflow["jobs"]["operations"]["steps"]
            if step.get("name") == "Run operations contract tests"
        )
        self.assertEqual(
            operations_test["run"],
            "python -m unittest discover -s tests/ops -p 'test_*.py' -v",
        )
        self.assertGreaterEqual(len(list((ROOT / "tests" / "ops").glob("test_*.py"))), 3)

    def test_ci_retains_browser_and_security_reports_and_caches_lockfiles(self) -> None:
        workflow = load_workflow("ci.yml")
        upload_steps = [
            step
            for job in workflow["jobs"].values()
            for step in job.get("steps", [])
            if str(step.get("uses", "")).startswith("actions/upload-artifact@")
        ]
        names = {step["with"]["name"] for step in upload_steps}
        self.assertEqual(names, {"playwright-report", "security-reports"})
        for step in upload_steps:
            self.assertEqual(step["with"]["retention-days"], 14)
        browser_upload = next(step for step in upload_steps if step["with"]["name"] == "playwright-report")
        self.assertEqual(
            browser_upload["with"]["path"].splitlines(),
            ["tests/e2e/playwright-report", "tests/e2e/test-results"],
        )

        setup_steps = [
            step
            for job in workflow["jobs"].values()
            for step in job.get("steps", [])
            if str(step.get("uses", "")).startswith(("actions/setup-node@", "actions/setup-go@"))
        ]
        dependency_paths = "\n".join(str(step.get("with", {}).get("cache-dependency-path", "")) for step in setup_steps)
        self.assertIn("apps/web/package-lock.json", dependency_paths)
        self.assertIn("tests/e2e/package-lock.json", dependency_paths)
        self.assertIn("apps/api/go.sum", dependency_paths)

    def test_publish_emits_three_application_images_and_one_backup_image(self) -> None:
        workflow = load_workflow("ci.yml")
        publish = workflow["jobs"]["publish"]
        self.assertEqual(publish["permissions"], {"contents": "read", "packages": "write"})
        condition = publish["if"]
        self.assertIn("github.ref_protected == true", condition)
        self.assertIn("refs/heads/main", condition)
        self.assertEqual(publish["needs"], ["contracts", "backend", "frontend", "runtime", "e2e", "infra", "operations"])

        build_steps = [
            step for step in publish["steps"] if str(step.get("uses", "")).startswith("docker/build-push-action@")
        ]
        self.assertEqual([step["id"] for step in build_steps], ["api", "migration", "web", "backup"])
        self.assertEqual([step["with"].get("target") for step in build_steps[:3]], ["api", "migration", "web"])
        self.assertEqual(build_steps[3]["with"].get("context"), "ops/backup")
        self.assertIsNone(build_steps[3]["with"].get("target"))
        for step in build_steps:
            values = step["with"]
            self.assertTrue(values["push"])
            self.assertTrue(values["sbom"])
            self.assertEqual(values["provenance"], "mode=max")
            self.assertRegex(values["tags"], r":\$\{\{ github\.sha \}\}$")
            self.assertIn("org.opencontainers.image.revision=${{ github.sha }}", values["labels"])
            self.assertIn(
                "org.opencontainers.image.source=https://github.com/${{ github.repository }}",
                values["labels"],
            )
        for step in build_steps[:3]:
            self.assertIn("BUILD_VERSION=${{ github.sha }}", step["with"]["build-args"])
            self.assertIn("VCS_REF=${{ github.sha }}", step["with"]["build-args"])
        self.assertNotIn(":latest", read(".github/workflows/ci.yml").lower())

        step_names = [step["name"] for step in publish["steps"]]
        scan_index = step_names.index("Scan the published immutable digests")
        eligibility_index = step_names.index("Publish the scanned release eligibility manifest")
        self.assertLess(scan_index, eligibility_index)
        scan_step = publish["steps"][scan_index]
        self.assertEqual(scan_step["run"], "bash scripts/delivery/scan-images.sh")
        self.assertIn("GHCR_TOKEN", scan_step["env"])
        eligibility_step = publish["steps"][eligibility_index]
        self.assertIn("docker buildx imagetools create", eligibility_step["run"])
        self.assertIn("io.alright-mate.image.backup", eligibility_step["run"])
        self.assertIn("io.alright-mate.migration.rollback-safe", eligibility_step["run"])

    def test_private_registry_scan_uses_disposable_credentials_without_logging_them(self) -> None:
        source = read("scripts/delivery/scan-images.sh")
        self.assertIn("mktemp -d", source)
        self.assertIn("chmod 0700", source)
        self.assertIn("DOCKER_CONFIG", source)
        self.assertIn("--password-stdin", source)
        self.assertIn("/root/.docker/config.json:ro", source)
        self.assertNotIn("set -x", source)
        self.assertNotRegex(source, r"docker login[^\n]*\$\{?GHCR_TOKEN")
        self.assertRegex(source, r"trap\s+cleanup\s+EXIT")

    def test_manual_deploy_and_rollback_are_serialised_production_jobs(self) -> None:
        deploy = load_workflow("deploy.yml")
        release_input = deploy["on"]["workflow_dispatch"]["inputs"]["release_sha"]
        self.assertTrue(release_input["required"])
        self.assertEqual(release_input["type"], "string")
        deploy_job = deploy["jobs"]["deploy"]
        self.assertEqual(deploy_job["environment"], "production")
        self.assertEqual(deploy_job["permissions"], {"contents": "read"})
        self.assertIn("github.ref == 'refs/heads/main'", deploy_job["if"])
        self.assertIn("github.ref_protected == true", deploy_job["if"])
        self.assertFalse(deploy["concurrency"]["cancel-in-progress"])
        deploy_checkout = next(
            step for step in deploy_job["steps"] if str(step.get("uses", "")).startswith("actions/checkout@")
        )
        self.assertEqual(deploy_checkout["with"]["ref"], "refs/heads/main")
        deploy_inputs = deploy["on"]["workflow_dispatch"]["inputs"]
        self.assertFalse(deploy_inputs["allow_unsafe_migration_rollback"]["default"])
        self.assertEqual(deploy_inputs["allow_unsafe_migration_rollback"]["type"], "boolean")
        self.assertIn("unsafe_migration_reason", deploy_inputs)
        self.assertFalse(deploy_inputs["operation_id"]["required"])
        self.assertEqual(deploy_inputs["operation_id"]["type"], "string")

        rollback = load_workflow("rollback.yml")
        self.assertIn("workflow_dispatch", rollback["on"])
        rollback_job = rollback["jobs"]["rollback"]
        self.assertEqual(rollback_job["environment"], "production")
        self.assertEqual(rollback_job["permissions"], {"contents": "read"})
        self.assertIn("github.ref == 'refs/heads/main'", rollback_job["if"])
        self.assertIn("github.ref_protected == true", rollback_job["if"])
        rollback_checkout = next(
            step for step in rollback_job["steps"] if str(step.get("uses", "")).startswith("actions/checkout@")
        )
        self.assertEqual(rollback_checkout["with"]["ref"], "refs/heads/main")
        rollback_inputs = rollback["on"]["workflow_dispatch"]["inputs"]
        self.assertFalse(rollback_inputs["allow_unsafe_migration_rollback"]["default"])
        self.assertIn("unsafe_migration_reason", rollback_inputs)
        self.assertFalse(rollback_inputs["operation_id"]["required"])
        self.assertEqual(rollback_inputs["operation_id"]["type"], "string")
        for workflow, job_name, command in (
            (deploy, "deploy", "release.sh deploy"),
            (rollback, "rollback", "release.sh rollback"),
        ):
            delivery_step = next(
                step
                for step in workflow["jobs"][job_name]["steps"]
                if command in str(step.get("run", ""))
            )
            self.assertEqual(
                delivery_step["env"]["ALLOW_UNSAFE_MIGRATION_ROLLBACK"],
                "${{ inputs.allow_unsafe_migration_rollback }}",
            )
            self.assertEqual(
                delivery_step["env"]["UNSAFE_MIGRATION_REASON"],
                "${{ inputs.unsafe_migration_reason }}",
            )
            self.assertEqual(
                delivery_step["env"]["DELIVERY_OPERATION_ID"],
                "${{ inputs.operation_id }}",
            )
        self.assertEqual(rollback["concurrency"]["group"], deploy["concurrency"]["group"])
        self.assertFalse(rollback["concurrency"]["cancel-in-progress"])

    def test_workflows_inject_required_secrets_without_printing_them(self) -> None:
        source = "\n".join(path.read_text(encoding="utf-8") for path in sorted(WORKFLOWS.glob("*.yml")))
        for name in (
            "PRODUCTION_SSH_HOST",
            "PRODUCTION_SSH_USER",
            "PRODUCTION_SSH_PRIVATE_KEY",
            "PRODUCTION_DELIVERY_SSH_USER",
            "PRODUCTION_DELIVERY_SSH_PRIVATE_KEY",
            "PRODUCTION_SSH_KNOWN_HOSTS",
            "PRODUCTION_RUNTIME_ENV",
            "PRODUCTION_GHCR_USERNAME",
            "PRODUCTION_GHCR_TOKEN",
            "PRODUCTION_BASE_URL",
        ):
            self.assertIn(f"secrets.{name}", source)
        self.assertNotRegex(source, r"(?im)^\s*(echo|printf).*(secrets\.|PRODUCTION_)")
        self.assertNotIn("set -x", source)
        self.assertNotIn("printenv", source)

    def test_release_controller_orders_backup_before_mutation_and_has_bounded_rollback(self) -> None:
        source = read("scripts/delivery/release.sh")
        deploy_branch = source[source.index("deploy_release()") : source.index("rollback_release()")]
        self.assertLess(deploy_branch.index("begin-deploy"), deploy_branch.index("run_ansible_release"))
        self.assertLess(deploy_branch.index("run_ansible_release"), deploy_branch.index("wait_for_readiness"))
        self.assertLess(deploy_branch.index("wait_for_readiness"), deploy_branch.index("finish_with_reconciliation"))
        self.assertIn("rollback_after_failure", deploy_branch)
        self.assertIn('run_remote abort "$operation_id" controller-exit', source)
        self.assertIn("new_operation_id", source)
        self.assertIn("run_remote status", source)
        self.assertIn("finish_with_reconciliation", source)
        self.assertIn("abort_with_reconciliation", source)
        self.assertIn("ALLOW_UNSAFE_MIGRATION_ROLLBACK", source)
        self.assertIn("UNSAFE_MIGRATION_REASON", source)
        self.assertIn("migration_rollback_safe", source)
        self.assertIn("docker_config_directory", source)
        self.assertIn('DOCKER_CONFIG="$docker_config_directory"', source)
        self.assertLess(
            deploy_branch.index("release_lock_active=1", deploy_branch.index("begin-deploy")),
            deploy_branch.index("begin_with_reconciliation"),
        )
        rollback_branch = source[source.index("rollback_release()") : source.index("validate_inputs\n")]
        self.assertLess(
            rollback_branch.index("release_lock_active=1", rollback_branch.index("begin-rollback")),
            rollback_branch.index("begin_with_reconciliation"),
        )
        self.assertIn("READINESS_TIMEOUT_SECONDS", source)
        self.assertRegex(source, r"READINESS_TIMEOUT_SECONDS:=\d+")
        self.assertNotRegex(source, r"(?i)(goose[ \t]+down|pg_restore|psql[^\n]*rollback)")

    def test_controller_uses_the_deploy_play_group_and_fail_closed_ansible_context(self) -> None:
        source = read("scripts/delivery/release.sh")
        deploy_playbook = yaml.safe_load(read("infra/ansible/playbooks/deploy.yml"))

        self.assertEqual(deploy_playbook[0]["hosts"], "alright_mate")
        self.assertIn("printf '[alright_mate]\\n", source)
        self.assertNotIn("printf '[production]\\n", source)
        self.assertIn("ANSIBLE_HOST_PATTERN_MISMATCH=error", source)
        self.assertIn(
            'ANSIBLE_FILTER_PLUGINS="${repository_root}/infra/ansible/filter_plugins"',
            source,
        )

    def test_ci_executes_a_real_one_host_and_deploy_filter_context(self) -> None:
        workflow = load_workflow("ci.yml")
        infra_commands = "\n".join(
            str(step.get("run", "")) for step in workflow["jobs"]["infra"]["steps"]
        )

        self.assertIn("verify-delivery-controller.sh", infra_commands)
        context_runner = read("infra/tests/ansible/verify-delivery-controller.sh")
        self.assertIn("delivery-controller-context.yml", context_runner)
        self.assertIn("ANSIBLE_HOST_PATTERN_MISMATCH=error", context_runner)
        self.assertIn("ANSIBLE_FILTER_PLUGINS", context_runner)
        context_playbook = read("infra/tests/ansible/delivery-controller-context.yml")
        self.assertIn("runtime_environment_reserved_keys", context_playbook)
        self.assertIn("release_image_revision_mismatches", context_playbook)

    def test_controller_uses_a_constrained_remote_helper_and_exact_runtime_proof(self) -> None:
        source = read("scripts/delivery/release.sh")
        remote = read("scripts/delivery/remote-release.sh")
        deploy_branch = source[source.index("deploy_release()") : source.index("rollback_release()")]

        self.assertIn("remote_protocol=alright-mate-delivery-v1", source)
        self.assertIn('base64.b64encode(b"\\0".join(arguments) + b"\\0")', source)
        self.assertNotIn('sudo -n "$remote_helper_path"', source)
        self.assertNotIn("sudo -n /bin/bash -s", source)
        self.assertNotIn('cat "${script_directory}/remote-release.sh"', source)
        self.assertIn("verify_runtime_release", source)
        self.assertLess(deploy_branch.index("run_ansible_release"), deploy_branch.index("verify_runtime_release"))
        self.assertLess(deploy_branch.index("verify_runtime_release"), deploy_branch.index("wait_for_readiness"))
        self.assertIn("verify-runtime)", remote)
        self.assertIn("runtime_identity=verified", remote)

    def test_operation_protocol_versions_and_audits_the_unsafe_override_reason(self) -> None:
        controller = read("scripts/delivery/release.sh")
        remote = read("scripts/delivery/remote-release.sh")

        self.assertIn('"$ALLOW_UNSAFE_MIGRATION_ROLLBACK" "$UNSAFE_MIGRATION_REASON"', controller)
        self.assertIn('"operation_schema=2"', remote)
        self.assertIn('"unsafe_migration_override=${unsafe_override}"', remote)
        self.assertIn('"unsafe_migration_reason=${unsafe_reason}"', remote)
        self.assertIn("unsafe_migration_reason=", remote)


class DeliveryScriptBehaviourTests(unittest.TestCase):
    def run_bash(self, command: str, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        execution_env = {**os.environ, **(env or {})}
        if os.name == "nt":
            python = shutil.which("python")
            if python:
                execution_env.setdefault("PYTHON_BIN", bash_path(Path(python)))
        return subprocess.run(
            [find_bash(), "--noprofile", "--norc", "-c", command],
            cwd=ROOT,
            env=execution_env,
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )

    def controller_fixture(
        self, temporary: Path, *, ssh_fault: str, initial_release: bool = False
    ) -> tuple[dict[str, str], Path, Path, Path]:
        state_root = temporary / "state"
        operations_root = state_root / "operations"
        operations_root.mkdir(parents=True)
        systemd_root = temporary / "systemd"
        (systemd_root / "alright-mate-backup.service.d").mkdir(parents=True)
        fake_bin = temporary / "bin"
        fake_bin.mkdir()
        events = temporary / "events.log"
        runtime_state = temporary / "runtime-state.env"
        durable_backup_release_environment = temporary / "durable-backup-release.env"
        install_root = temporary / "install"
        install_root.mkdir()
        if not initial_release:
            runtime_state.write_text(
                textwrap.dedent(
                    f"""\
                    release_sha={PREVIOUS_SHA}
                    api_image_reference={PREVIOUS_API_REFERENCE}
                    migration_image_reference={PREVIOUS_MIGRATION_REFERENCE}
                    web_image_reference={PREVIOUS_WEB_REFERENCE}
                    backup_image_reference={PREVIOUS_BACKUP_REFERENCE}
                    """
                ),
                encoding="utf-8",
                newline="\n",
            )
            durable_backup_release_environment.write_text(
                f"RELEASE_SHA={PREVIOUS_SHA}\nBACKUP_TOOLS_IMAGE={PREVIOUS_BACKUP_REFERENCE}\n",
                encoding="utf-8",
                newline="\n",
            )
            state_root.joinpath("release.env").write_text(
                textwrap.dedent(
                    f"""\
                    current={PREVIOUS_SHA}
                    current_api={PREVIOUS_API_REFERENCE}
                    current_migration={PREVIOUS_MIGRATION_REFERENCE}
                    current_web={PREVIOUS_WEB_REFERENCE}
                    current_backup={PREVIOUS_BACKUP_REFERENCE}
                    current_rollback_safe=true
                    previous={OLDER_SHA}
                    previous_api=ghcr.io/example/alright-mate-api@sha256:{'4' * 64}
                    previous_migration=ghcr.io/example/alright-mate-migration@sha256:{'5' * 64}
                    previous_web=ghcr.io/example/alright-mate-web@sha256:{'6' * 64}
                    previous_backup=ghcr.io/example/alright-mate-backup@sha256:{'8' * 64}
                    previous_rollback_safe=true
                    updated_at=fixture
                    """
                ),
                encoding="utf-8",
                newline="\n",
            )
        marker = json.dumps(
            {
                "schemaVersion": 2,
                "annotations": {
                    "io.alright-mate.release.sha": SHA,
                    "io.alright-mate.image.api": API_REFERENCE,
                    "io.alright-mate.image.migration": MIGRATION_REFERENCE,
                    "io.alright-mate.image.web": WEB_REFERENCE,
                    "io.alright-mate.image.backup": BACKUP_REFERENCE,
                    "io.alright-mate.migration.rollback-safe": "true",
                },
            },
            separators=(",", ":"),
        )

        fixtures = {
            "df": textwrap.dedent(
                """\
                #!/bin/sh
                if [ "${DELIVERY_CHECK_DISK_CRASH:-false}" = true ]; then
                  kill -KILL "$PPID"
                  exit 137
                fi
                printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\nfixture 9999999 1 9999998 1%% /opt/alright-mate\n'
                """
            ),
            "docker": textwrap.dedent(
                f"""\
                #!/bin/sh
                config=${{DOCKER_CONFIG:-unset}}
                mode=unset
                if [ "$config" != unset ] && [ -d "$config" ]; then mode=$(stat -c %a "$config" 2>/dev/null || echo unknown); fi
                printf 'docker mode=%s args=%s\n' "$mode" "$*" >>'{bash_path(events)}'
                case "$1" in
                  login)
                    token=$(cat)
                    printf '{{"auth":"%s"}}' "$token" >"$DOCKER_CONFIG/config.json"
                    ;;
                  logout)
                    test -f "$DOCKER_CONFIG/config.json" && printf 'logout_had_config=yes\n' >>'{bash_path(events)}'
                    rm -f "$DOCKER_CONFIG/config.json"
                    ;;
                  buildx)
                    reference=${{5:-}}
                    case "$reference" in
                      ghcr.io/example/alright-mate-release:{SHA}) printf '%s' '{marker}' ;;
                      {API_REFERENCE}|{MIGRATION_REFERENCE}|{WEB_REFERENCE}|{BACKUP_REFERENCE}) printf '{{}}' ;;
                      *) exit 69 ;;
                    esac
                    ;;
                  info) ;;
                  ps)
                    [ -f "$DELIVERY_FAKE_RUNTIME_STATE" ] || exit 0
                    case "$*" in
                      *service=api*) printf 'fixture-api\n' ;;
                      *service=web*) printf 'fixture-web\n' ;;
                      *service=migrate*) printf 'fixture-migrate\n' ;;
                      *service=db*) printf 'fixture-db\n' ;;
                      *service=gateway*) printf 'fixture-gateway\n' ;;
                      *) exit 10 ;;
                    esac
                    ;;
                  inspect)
                    format=${{3:-}}
                    container=${{4:-}}
                    case "$format:$container" in
                      *Config.Image*:*fixture-api)
                        if [ "{ssh_fault}" = runtime-digest-mismatch ] && grep -q '^release_sha={SHA}$' "$DELIVERY_FAKE_RUNTIME_STATE"; then
                          printf '%s\n' '{PREVIOUS_API_REFERENCE}'
                        else
                          awk -F= '$1 == "api_image_reference" {{ print substr($0, index($0, "=") + 1) }}' "$DELIVERY_FAKE_RUNTIME_STATE"
                        fi
                        ;;
                      *Config.Image*:*fixture-migrate) awk -F= '$1 == "migration_image_reference" {{ print substr($0, index($0, "=") + 1) }}' "$DELIVERY_FAKE_RUNTIME_STATE" ;;
                      *Config.Image*:*fixture-web) awk -F= '$1 == "web_image_reference" {{ print substr($0, index($0, "=") + 1) }}' "$DELIVERY_FAKE_RUNTIME_STATE" ;;
                      *State.ExitCode*:*fixture-migrate) printf 'exited 0\n' ;;
                      *State.Health*:*fixture-*) printf 'running healthy\n' ;;
                      *) exit 11 ;;
                    esac
                    ;;
                  image) [ "${{2:-}}" = inspect ] ;;
                  *) exit 9 ;;
                esac
                """
            ),
            "systemctl": textwrap.dedent(
                f"""\
                #!/bin/sh
                printf 'systemctl %s\n' "$*" >>'{bash_path(events)}'
                case "$1" in
                  start)
                    if [ "${{DELIVERY_FAKE_BACKUP_CRASH:-false}}" = true ]; then
                      kill -KILL "$PPID"
                      exit 137
                    fi
                    ;;
                  show) case "$*" in *ExecMainStatus*) echo 0;; *) echo success;; esac ;;
                esac
                """
            ),
            "ssh-keygen": "#!/bin/sh\nexit 0\n",
            "git": "#!/bin/sh\ncase \"$*\" in *cat-file*|*merge-base*|*archive*) exit 0;; *) exit 9;; esac\n",
            "tar": "#!/bin/sh\ncat >/dev/null\nexit 0\n",
            "curl": "#!/bin/sh\nexit 0\n",
            "ansible-playbook": textwrap.dedent(
                f"""\
                #!/bin/sh
                printf 'ansible %s\n' "$*" >>'{bash_path(events)}'
                inventory=
                previous=
                for argument in "$@"; do
                  if [ "$previous" = --inventory ]; then inventory=$argument; fi
                  previous=$argument
                done
                test -f "$inventory" || exit 40
                grep -q "ansible_user=$DELIVERY_EXPECTED_ANSIBLE_USER" "$inventory" || exit 40
                printf 'ansible-user=%s\n' "$DELIVERY_EXPECTED_ANSIBLE_USER" >>'{bash_path(events)}'
                case "{ssh_fault}" in
                  restoration-fails) exit 41 ;;
                  abort-unknown)
                    case "$*" in *release_sha={SHA}*) exit 42 ;; esac
                    ;;
                esac
                release_sha=
                api_image_reference=
                migration_image_reference=
                web_image_reference=
                backup_image_reference=
                for argument in "$@"; do
                  case "$argument" in
                    release_sha=*) release_sha=${{argument#release_sha=}} ;;
                    api_image_reference=*) api_image_reference=${{argument#api_image_reference=}} ;;
                    migration_image_reference=*) migration_image_reference=${{argument#migration_image_reference=}} ;;
                    web_image_reference=*) web_image_reference=${{argument#web_image_reference=}} ;;
                    backup_image_reference=*) backup_image_reference=${{argument#backup_image_reference=}} ;;
                  esac
                done
                test -n "$release_sha" && test -n "$api_image_reference" &&
                  test -n "$migration_image_reference" && test -n "$web_image_reference" &&
                  test -n "$backup_image_reference" || exit 43
                printf 'release_sha=%s\napi_image_reference=%s\nmigration_image_reference=%s\nweb_image_reference=%s\nbackup_image_reference=%s\n' \
                  "$release_sha" "$api_image_reference" "$migration_image_reference" \
                  "$web_image_reference" "$backup_image_reference" >"$DELIVERY_FAKE_RUNTIME_STATE"
                printf 'RELEASE_SHA=%s\nBACKUP_TOOLS_IMAGE=%s\n' \
                  "$release_sha" "$backup_image_reference" >"$DELIVERY_DURABLE_BACKUP_RELEASE_ENV_FILE"
                if [ "{ssh_fault}" = signal-after-mutation ]; then
                  kill -TERM "$PPID"
                  exit 143
                fi
                exit 0
                """
            ),
            "readlink": textwrap.dedent(
                """\
                #!/bin/sh
                case "${2:-${1:-}}" in
                  */current)
                    [ -f "$DELIVERY_FAKE_RUNTIME_STATE" ] || exit 0
                    root=${2:-$1}
                    root=${root%/current}
                    sha=$(awk -F= '$1 == "release_sha" { print substr($0, index($0, "=") + 1) }' "$DELIVERY_FAKE_RUNTIME_STATE")
                    printf '%s/releases/%s\n' "$root" "$sha"
                    ;;
                  *) printf '%s\n' "${2:-$1}" ;;
                esac
                """
            ),
            "ssh": textwrap.dedent(
                f"""\
                #!/usr/bin/env bash
                set -u
                remote_command=
                remote_target=
                for argument in "$@"; do
                  remote_command=$argument
                  if [ "$argument" = "$DELIVERY_EXPECTED_SSH_TARGET" ]; then remote_target=$argument; fi
                done
                [ "$remote_target" = "$DELIVERY_EXPECTED_SSH_TARGET" ] || exit 63
                printf 'ssh-target=%s\n' "$remote_target" >>'{bash_path(events)}'
                printf 'ssh %s\n' "$remote_command" >>'{bash_path(events)}'
                case "$remote_command" in
                  alright-mate-delivery-v1\ *) payload_token=${{remote_command#alright-mate-delivery-v1 }} ;;
                  *) exit 64 ;;
                esac
                [[ "$payload_token" =~ ^[A-Za-z0-9+/]+={{0,2}}$ ]] || exit 64
                payload_file='{bash_path(temporary / 'ssh-payload.bin')}'
                printf '%s' "$payload_token" | base64 --decode >"$payload_file" || exit 64
                mapfile -d '' -t helper_arguments <"$payload_file"
                rm -f -- "$payload_file"
                set -- "${{helper_arguments[@]}}"
                printf 'helper /usr/local/libexec/alright-mate-delivery' >>'{bash_path(events)}'
                printf ' %s' "$@" >>'{bash_path(events)}'
                printf '\n' >>'{bash_path(events)}'
                first=${{1:-}}
                case "{ssh_fault}:$first" in
                  begin-preparing-response-lost:begin-deploy)
                    marker='{bash_path(temporary / 'begin-preparing-lost.marker')}'
                    if [ ! -f "$marker" ]; then
                      : >"$marker"
                      DELIVERY_FAKE_BACKUP_CRASH=true bash "$DELIVERY_FAKE_REMOTE_HELPER" "$@" >/dev/null 2>&1
                      exit 255
                    fi
                    ;;
                  begin-lost-once:begin-deploy)
                    marker='{bash_path(temporary / 'begin-lost.marker')}'
                    if [ ! -f "$marker" ]; then
                      bash "$DELIVERY_FAKE_REMOTE_HELPER" "$@" >/dev/null
                      status=$?
                      : >"$marker"
                      [ "$status" -eq 0 ] || exit "$status"
                      exit 255
                    fi
                    ;;
                  finish-lost-once:finish)
                    marker='{bash_path(temporary / 'finish-lost.marker')}'
                    if [ ! -f "$marker" ]; then
                      bash "$DELIVERY_FAKE_REMOTE_HELPER" "$@" >/dev/null
                      status=$?
                      : >"$marker"
                      [ "$status" -eq 0 ] || exit "$status"
                      exit 255
                    fi
                    ;;
                  finish-commit-status-lost:finish)
                    marker='{bash_path(temporary / 'finish-committed.marker')}'
                    if [ ! -f "$marker" ]; then
                      bash "$DELIVERY_FAKE_REMOTE_HELPER" "$@" >/dev/null
                      status=$?
                      : >"$marker"
                      [ "$status" -eq 0 ] || exit "$status"
                    fi
                    exit 255
                    ;;
                  finish-commit-status-lost:status) exit 255 ;;
                  finish-never:finish) exit 255 ;;
                  abort-unknown:abort) exit 255 ;;
                  abort-unknown:status) exit 255 ;;
                esac
                bash "$DELIVERY_FAKE_REMOTE_HELPER" "$@"
                """
            ),
        }
        for name, source in fixtures.items():
            executable = fake_bin / name
            executable.write_text(source, encoding="utf-8", newline="\n")
        bash_environment = temporary / "bash-env.sh"
        bash_environment.write_text(
            "\n".join(
                f'{name}() {{ bash "$DELIVERY_FAKE_BIN/{name}" "$@"; }}'
                for name in fixtures
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )

        environment = {
            "ALLOW_UNSAFE_MIGRATION_ROLLBACK": "false",
            "BASH_ENV": bash_path(bash_environment),
            "DELIVERY_FAKE_BIN": bash_path(fake_bin),
            "DELIVERY_FAKE_REMOTE_HELPER": bash_path(SCRIPTS / "remote-release.sh"),
            "DELIVERY_FAKE_RUNTIME_STATE": bash_path(runtime_state),
            "DELIVERY_OPERATION_ID": OPERATION_ID,
            "DELIVERY_BACKUP_RELEASE_ENV_FILE": bash_path(temporary / "backup-release.env"),
            "DELIVERY_DURABLE_BACKUP_RELEASE_ENV_FILE": bash_path(durable_backup_release_environment),
            "DELIVERY_INSTALL_ROOT": bash_path(install_root),
            "DELIVERY_STATE_ROOT": bash_path(state_root),
            "DELIVERY_SYSTEMD_RUNTIME_ROOT": bash_path(systemd_root),
            "DELIVERY_EXPECTED_ANSIBLE_USER": "operator",
            "DELIVERY_EXPECTED_SSH_TARGET": "delivery@example.test",
            "GHCR_REPOSITORY_PREFIX": "ghcr.io/example/alright-mate",
            "PRODUCTION_BASE_URL": "https://example.test",
            "PRODUCTION_GHCR_TOKEN": "controller-secret-token",
            "PRODUCTION_GHCR_USERNAME": "fixture-owner",
            "PRODUCTION_RUNTIME_ENV": "POSTGRES_PASSWORD=fixture",
            "PRODUCTION_SSH_HOST": "example.test",
            "PRODUCTION_SSH_KNOWN_HOSTS": "example.test ssh-ed25519 fixture",
            "PRODUCTION_DELIVERY_SSH_PRIVATE_KEY": "fixture-delivery-private-key",
            "PRODUCTION_DELIVERY_SSH_USER": "delivery",
            "PRODUCTION_SSH_PRIVATE_KEY": "fixture-operator-private-key",
            "PRODUCTION_SSH_USER": "operator",
            "READINESS_TIMEOUT_SECONDS": "30",
            "UNSAFE_MIGRATION_REASON": "",
        }
        return environment, state_root, events, fake_bin

    def test_resolver_rejects_non_sha_before_registry_access_and_resolves_all_images(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="task5b-resolver-", dir=temporary_parent()
        ) as directory:
            temporary = Path(directory)
            fake_bin = temporary / "bin"
            fake_bin.mkdir()
            calls = temporary / "calls.log"
            docker = fake_bin / "docker"
            api_reference = f"ghcr.io/example/alright-mate-api@sha256:{'a' * 64}"
            migration_reference = f"ghcr.io/example/alright-mate-migration@sha256:{'b' * 64}"
            web_reference = f"ghcr.io/example/alright-mate-web@sha256:{'c' * 64}"
            backup_reference = f"ghcr.io/example/alright-mate-backup@sha256:{'d' * 64}"
            marker = json.dumps(
                {
                    "schemaVersion": 2,
                    "annotations": {
                        "io.alright-mate.release.sha": SHA,
                        "io.alright-mate.image.api": api_reference,
                        "io.alright-mate.image.migration": migration_reference,
                        "io.alright-mate.image.web": web_reference,
                        "io.alright-mate.image.backup": backup_reference,
                        "io.alright-mate.migration.rollback-safe": "true",
                    },
                },
                separators=(",", ":"),
            )
            docker.write_text(
                textwrap.dedent(
                    f"""\
                    #!/bin/sh
                    printf '%s\\n' "$*" >>'{bash_path(calls)}'
                    case "$*" in
                      *"imagetools inspect --raw ghcr.io/example/alright-mate-release:{SHA}"*) printf '%s' '{marker}' ;;
                      *"imagetools inspect --raw {api_reference}"*) printf '{{}}' ;;
                      *"imagetools inspect --raw {migration_reference}"*) printf '{{}}' ;;
                      *"imagetools inspect --raw {web_reference}"*) printf '{{}}' ;;
                      *"imagetools inspect --raw {backup_reference}"*) printf '{{}}' ;;
                      *) exit 9 ;;
                    esac
                    """
                ),
                encoding="utf-8",
                newline="\n",
            )

            script = bash_path(SCRIPTS / "resolve-images.sh")
            path = bash_path(fake_bin)
            invalid = self.run_bash(
                f"PATH='{path}':$PATH GHCR_REPOSITORY_PREFIX='ghcr.io/example/alright-mate' bash '{script}' not-a-sha"
            )
            self.assertEqual(invalid.returncode, 64, invalid.stderr)
            self.assertFalse(calls.exists())

            ineligible = self.run_bash(
                f"PATH='{path}':$PATH GHCR_REPOSITORY_PREFIX='ghcr.io/example/alright-mate' bash '{script}' '{PREVIOUS_SHA}'"
            )
            self.assertEqual(ineligible.returncode, 69, ineligible.stderr)
            self.assertEqual(len(calls.read_text(encoding="utf-8").splitlines()), 1)
            calls.unlink()

            valid = self.run_bash(
                f"PATH='{path}':$PATH GHCR_REPOSITORY_PREFIX='ghcr.io/example/alright-mate' bash '{script}' '{SHA}'"
            )
            self.assertEqual(valid.returncode, 0, valid.stderr)
            self.assertEqual(
                valid.stdout.splitlines(),
                [
                    f"api_image_reference={api_reference}",
                    f"migration_image_reference={migration_reference}",
                    f"web_image_reference={web_reference}",
                    f"backup_image_reference={backup_reference}",
                    "migration_rollback_safe=true",
                ],
            )
            registry_calls = calls.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(registry_calls), 5)
            self.assertIn(f"-release:{SHA}", registry_calls[0])
            self.assertTrue(all("@sha256:" in line for line in registry_calls[1:]))

            tampered_marker = marker.replace(
                api_reference,
                f"ghcr.io/attacker/alright-mate-api@sha256:{'a' * 64}",
            )
            docker.write_text(
                docker.read_text(encoding="utf-8").replace(marker, tampered_marker),
                encoding="utf-8",
                newline="\n",
            )
            calls.unlink()
            tampered = self.run_bash(
                f"PATH='{path}':$PATH GHCR_REPOSITORY_PREFIX='ghcr.io/example/alright-mate' bash '{script}' '{SHA}'"
            )
            self.assertNotEqual(tampered.returncode, 0)
            self.assertIn("another repository", tampered.stderr)
            self.assertEqual(len(calls.read_text(encoding="utf-8").splitlines()), 1)

    def test_migration_declaration_is_bound_to_the_exact_migration_tree(self) -> None:
        script = bash_path(SCRIPTS / "migration-compatibility.sh")
        accepted = self.run_bash(f"bash '{script}'")
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertIn("migration_rollback_safe=true", accepted.stdout)
        self.assertIn("migration_tree_sha256=3f766b52", accepted.stdout)

        with tempfile.TemporaryDirectory(
            prefix="task5b-compatibility-", dir=temporary_parent()
        ) as directory:
            declaration = Path(directory) / "migration-compatibility.env"
            declaration.write_text(
                "schema=1\n"
                f"migration_tree_sha256={'0' * 64}\n"
                "rollback_safe=true\n",
                encoding="utf-8",
                newline="\n",
            )
            rejected = self.run_bash(f"bash '{script}' '{bash_path(declaration)}'")
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("changed without a reviewed", rejected.stderr)

            declaration.write_text(
                "schema=1\n"
                "migration_tree_sha256=3f766b52ea3f6a543e24ed0b475c8a9a667a62087d2dec9ad9bf074cc6234cc4\n"
                "rollback_safe=false\n",
                encoding="utf-8",
                newline="\n",
            )
            unsafe = self.run_bash(f"bash '{script}' '{bash_path(declaration)}'")
            self.assertEqual(unsafe.returncode, 0, unsafe.stderr)
            self.assertIn("migration_rollback_safe=false", unsafe.stdout)

    def test_private_image_scan_cleans_isolated_credentials_on_success_and_failure(self) -> None:
        script = bash_path(SCRIPTS / "scan-images.sh")
        for injected_failure, expected_status in (("false", 0), ("true", 17)):
            with self.subTest(injected_failure=injected_failure), tempfile.TemporaryDirectory(
                prefix="task5b-scan-auth-", dir=temporary_parent()
            ) as directory:
                temporary = Path(directory)
                docker_script = temporary / "docker"
                bash_environment = temporary / "bash-env.sh"
                events = temporary / "events.log"
                docker_script.write_text(
                    textwrap.dedent(
                        f"""\
                        #!/bin/sh
                        mode=unset
                        if [ -n "${{DOCKER_CONFIG:-}}" ] && [ -d "$DOCKER_CONFIG" ]; then
                          mode=$(stat -c %a "$DOCKER_CONFIG" 2>/dev/null || echo unknown)
                        fi
                        case "$1" in
                          login)
                            token=$(cat)
                            printf '{{"auth":"%s"}}' "$token" >"$DOCKER_CONFIG/config.json"
                            printf 'login mode=%s\n' "$mode" >>'{bash_path(events)}'
                            ;;
                          logout)
                            test -f "$DOCKER_CONFIG/config.json" && printf 'logout_had_config=yes\n' >>'{bash_path(events)}'
                            rm -f "$DOCKER_CONFIG/config.json"
                            ;;
                          run)
                            authenticated=false
                            for argument in "$@"; do
                              case "$argument" in
                                *:/root/.docker/config.json:ro)
                                  host=${{argument%:/root/.docker/config.json:ro}}
                                  grep -q 'scan-secret-token' "$host" && authenticated=true
                                  ;;
                              esac
                            done
                            printf 'scan authenticated=%s\n' "$authenticated" >>'{bash_path(events)}'
                            [ "$authenticated" = true ] || exit 16
                            [ '{injected_failure}' = false ] || exit 17
                            ;;
                          *) exit 9 ;;
                        esac
                        """
                    ),
                    encoding="utf-8",
                    newline="\n",
                )
                bash_environment.write_text(
                    'docker() { bash "$DELIVERY_FAKE_DOCKER" "$@"; }\n',
                    encoding="utf-8",
                    newline="\n",
                )
                environment = {
                    "BASH_ENV": bash_path(bash_environment),
                    "DELIVERY_FAKE_DOCKER": bash_path(docker_script),
                    "GHCR_TOKEN": "scan-secret-token",
                    "GHCR_USERNAME": "fixture-owner",
                    "TRIVY_CACHE_VOLUME": "fixture-cache",
                    "TRIVY_IMAGES": f"{API_REFERENCE} {MIGRATION_REFERENCE} {WEB_REFERENCE} {BACKUP_REFERENCE}",
                }
                result = self.run_bash(f"bash '{script}'", env=environment)
                self.assertEqual(result.returncode, expected_status, result.stderr)
                event_text = events.read_text(encoding="utf-8")
                self.assertIn("login mode=700", event_text)
                self.assertIn("scan authenticated=true", event_text)
                self.assertIn("logout_had_config=yes", event_text)
                self.assertNotIn("scan-secret-token", event_text)

    def test_controller_reconciles_lost_finish_and_restores_on_unfinalised_release(self) -> None:
        script = bash_path(SCRIPTS / "release.sh")
        for fault, expected_status in (
            ("begin-lost-once", 0),
            ("finish-lost-once", 0),
            ("finish-commit-status-lost", 1),
            ("finish-never", 1),
        ):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory(
                prefix=f"task5b-controller-{fault}-", dir=temporary_parent()
            ) as directory:
                environment, state_root, events, fake_bin = self.controller_fixture(
                    Path(directory), ssh_fault=fault
                )
                result = self.run_bash(
                    f"bash '{script}' deploy '{SHA}'",
                    env=environment,
                )
                self.assertEqual(result.returncode, expected_status, result.stderr)
                state = (state_root / "release.env").read_text(encoding="utf-8")
                event_text = events.read_text(encoding="utf-8")
                operation = state_root / "operations" / f"{OPERATION_ID}.env"
                operation_text = operation.read_text(encoding="utf-8")
                self.assertNotIn("controller-secret-token", event_text)
                self.assertIn("docker mode=700 args=login", event_text)
                self.assertIn("logout_had_config=yes", event_text)
                self.assertIn(
                    f"verify-runtime {SHA} {API_REFERENCE} {MIGRATION_REFERENCE} {WEB_REFERENCE} {BACKUP_REFERENCE}",
                    event_text,
                )
                self.assertFalse((state_root / "pending.env").exists())
                self.assertFalse((state_root / "lock").exists())

                if fault in {"begin-lost-once", "finish-lost-once"}:
                    self.assertIn(f"current={SHA}\n", state)
                    self.assertIn(f"current_api={API_REFERENCE}\n", state)
                    self.assertIn(f"current_backup={BACKUP_REFERENCE}\n", state)
                    self.assertIn("operation_status=success", operation_text)
                    self.assertEqual(event_text.count(f"release_sha={SHA}"), 1)
                    self.assertIn(f"backup_image_reference={BACKUP_REFERENCE}", event_text)
                elif fault == "finish-never":
                    self.assertIn(f"current={PREVIOUS_SHA}\n", state)
                    self.assertIn(f"current_api={PREVIOUS_API_REFERENCE}\n", state)
                    self.assertIn(f"current_backup={PREVIOUS_BACKUP_REFERENCE}\n", state)
                    self.assertIn("operation_status=aborted", operation_text)
                    self.assertIn("outcome=finalisation-failed", operation_text)
                    self.assertIn("finalisation state=confirmed-pending", result.stderr)
                    self.assertEqual(event_text.count(f"release_sha={SHA}"), 1)
                    self.assertEqual(event_text.count(f"release_sha={PREVIOUS_SHA}"), 1)
                    self.assertIn(f"api_image_reference={PREVIOUS_API_REFERENCE}", event_text)
                    self.assertIn(f"backup_image_reference={PREVIOUS_BACKUP_REFERENCE}", event_text)
                    self.assertNotIn(f"-release:{PREVIOUS_SHA}", event_text)
                else:
                    self.assertIn(f"current={SHA}\n", state)
                    self.assertIn(f"current_api={API_REFERENCE}\n", state)
                    self.assertIn(f"current_backup={BACKUP_REFERENCE}\n", state)
                    self.assertIn("operation_status=success", operation_text)
                    self.assertEqual(event_text.count(f"release_sha={SHA}"), 1)
                    self.assertEqual(event_text.count(f"release_sha={PREVIOUS_SHA}"), 0)
                    reconciliation_output = result.stdout + result.stderr
                    self.assertIn("delivery_reconciliation=required", reconciliation_output)
                    self.assertIn(f"operation={OPERATION_ID}", reconciliation_output)

    def test_controller_reuses_an_operation_id_after_unknown_finalisation(self) -> None:
        script = bash_path(SCRIPTS / "release.sh")
        with tempfile.TemporaryDirectory(
            prefix="task5b-operation-replay-", dir=temporary_parent()
        ) as directory:
            environment, state_root, events, _fake_bin = self.controller_fixture(
                Path(directory), ssh_fault="finish-commit-status-lost"
            )
            first = self.run_bash(
                f"bash '{script}' deploy '{SHA}'",
                env=environment,
            )
            self.assertNotEqual(first.returncode, 0)
            self.assertIn(f"operation={OPERATION_ID}", first.stdout + first.stderr)

            replayed = self.run_bash(
                f"bash '{script}' deploy '{SHA}'",
                env=environment,
            )

            self.assertEqual(replayed.returncode, 0, replayed.stderr)
            self.assertIn("deployment=idempotent-success", replayed.stdout)
            operation = state_root / "operations" / f"{OPERATION_ID}.env"
            self.assertIn("operation_status=success", operation.read_text(encoding="utf-8"))
            self.assertFalse((state_root / "lock").exists())
            self.assertEqual(events.read_text(encoding="utf-8").count(f"release_sha={SHA}"), 1)

    def test_controller_surfaces_and_replays_a_generated_id_after_lost_preparation(self) -> None:
        script = bash_path(SCRIPTS / "release.sh")
        with tempfile.TemporaryDirectory(
            prefix="task5b-preparing-replay-", dir=temporary_parent()
        ) as directory:
            environment, state_root, events, _fake_bin = self.controller_fixture(
                Path(directory), ssh_fault="begin-preparing-response-lost"
            )
            environment.pop("DELIVERY_OPERATION_ID")

            first = self.run_bash(
                f"bash '{script}' deploy '{SHA}'",
                env=environment,
            )

            self.assertNotEqual(first.returncode, 0)
            operations = list((state_root / "operations").glob("*.env"))
            self.assertEqual(len(operations), 1)
            generated_operation_id = operations[0].stem
            self.assertRegex(generated_operation_id, r"^[0-9a-f]{32}$")
            first_output = first.stdout + first.stderr
            self.assertIn(f"delivery_operation={generated_operation_id}", first_output)
            self.assertIn(
                f"delivery_reconciliation=required operation={generated_operation_id} state=preparing",
                first_output,
            )
            self.assertIn("operation_status=preparing", operations[0].read_text(encoding="utf-8"))
            self.assertEqual(
                (state_root / "lock").read_text(encoding="utf-8").strip(),
                generated_operation_id,
            )
            first_events = events.read_text(encoding="utf-8")
            self.assertNotIn(
                f"helper /usr/local/libexec/alright-mate-delivery abort {generated_operation_id}",
                first_events,
            )

            environment["DELIVERY_OPERATION_ID"] = generated_operation_id
            replayed = self.run_bash(
                f"bash '{script}' deploy '{SHA}'",
                env=environment,
            )

            self.assertEqual(replayed.returncode, 0, replayed.stderr)
            self.assertIn(f"operation={generated_operation_id}", replayed.stdout)
            self.assertIn("operation_status=success", operations[0].read_text(encoding="utf-8"))
            self.assertFalse((state_root / "lock").exists())
            self.assertFalse((state_root / "pending.env").exists())
            event_text = events.read_text(encoding="utf-8")
            self.assertEqual(event_text.count("systemctl start alright-mate-backup.service"), 2)
            self.assertEqual(event_text.count(f"release_sha={SHA}"), 1)

    def test_controller_completes_a_fresh_host_initial_deploy_without_backup(self) -> None:
        script = bash_path(SCRIPTS / "release.sh")
        with tempfile.TemporaryDirectory(
            prefix="task5b-initial-deploy-", dir=temporary_parent()
        ) as directory:
            environment, state_root, events, _fake_bin = self.controller_fixture(
                Path(directory), ssh_fault="none", initial_release=True
            )
            result = self.run_bash(
                f"bash '{script}' deploy '{SHA}'",
                env=environment,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            state = (state_root / "release.env").read_text(encoding="utf-8")
            self.assertIn(f"current={SHA}\n", state)
            self.assertIn(f"previous={SHA}\n", state)
            self.assertIn(f"current_api={API_REFERENCE}\n", state)
            self.assertIn(f"previous_api={API_REFERENCE}\n", state)
            self.assertIn(f"current_backup={BACKUP_REFERENCE}\n", state)
            self.assertIn(f"previous_backup={BACKUP_REFERENCE}\n", state)
            operation = state_root / "operations" / f"{OPERATION_ID}.env"
            operation_text = operation.read_text(encoding="utf-8")
            self.assertIn("initial_deploy=true", operation_text)
            self.assertIn("operation_status=success", operation_text)
            self.assertFalse((state_root / "pending.env").exists())
            self.assertFalse((state_root / "lock").exists())
            event_text = events.read_text(encoding="utf-8")
            self.assertNotIn("systemctl start alright-mate-backup.service", event_text)
            self.assertEqual(event_text.count(f"release_sha={SHA}"), 1)

    def test_controller_separates_operator_and_constrained_delivery_identities(self) -> None:
        script = bash_path(SCRIPTS / "release.sh")
        with tempfile.TemporaryDirectory(
            prefix="task5b-delivery-identity-", dir=temporary_parent()
        ) as directory:
            environment, _state_root, events, _fake_bin = self.controller_fixture(
                Path(directory), ssh_fault="none"
            )
            result = self.run_bash(
                f"bash '{script}' deploy '{SHA}'",
                env=environment,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            event_text = events.read_text(encoding="utf-8")
            self.assertIn("ansible-user=operator", event_text)
            self.assertIn("ssh-target=delivery@example.test", event_text)

    def test_controller_signal_after_mutation_preserves_operation_for_reconciliation(self) -> None:
        script = bash_path(SCRIPTS / "release.sh")
        with tempfile.TemporaryDirectory(
            prefix="task5b-mutation-signal-", dir=temporary_parent()
        ) as directory:
            environment, state_root, events, _fake_bin = self.controller_fixture(
                Path(directory), ssh_fault="signal-after-mutation"
            )
            result = self.run_bash(
                f"bash '{script}' deploy '{SHA}'",
                env=environment,
            )

            self.assertNotEqual(result.returncode, 0)
            reconciliation_output = result.stdout + result.stderr
            self.assertIn("delivery_reconciliation=required", reconciliation_output)
            self.assertIn(f"operation={OPERATION_ID}", reconciliation_output)
            operation = state_root / "operations" / f"{OPERATION_ID}.env"
            self.assertIn("operation_status=pending", operation.read_text(encoding="utf-8"))
            self.assertEqual(
                (state_root / "lock").read_text(encoding="utf-8").strip(),
                OPERATION_ID,
            )
            self.assertTrue((state_root / "pending.env").is_file())
            state = (state_root / "release.env").read_text(encoding="utf-8")
            self.assertIn(f"current={PREVIOUS_SHA}\n", state)
            event_text = events.read_text(encoding="utf-8")
            self.assertNotIn(
                f"/usr/local/libexec/alright-mate-delivery abort {OPERATION_ID}",
                event_text,
            )

    def test_direct_finish_rejects_a_runtime_that_does_not_match_the_operation(self) -> None:
        helper = bash_path(SCRIPTS / "remote-release.sh")
        with tempfile.TemporaryDirectory(
            prefix="task5b-direct-finish-", dir=temporary_parent()
        ) as directory:
            environment, state_root, _events, _fake_bin = self.controller_fixture(
                Path(directory), ssh_fault="none"
            )
            begun = self.run_bash(
                f"bash '{helper}' begin-deploy '{OPERATION_ID}' '{SHA}' "
                f"'{API_REFERENCE}' '{MIGRATION_REFERENCE}' '{WEB_REFERENCE}' "
                f"'{BACKUP_REFERENCE}' true false ''",
                env=environment,
            )
            self.assertEqual(begun.returncode, 0, begun.stderr)

            finished = self.run_bash(
                f"bash '{helper}' finish '{OPERATION_ID}'",
                env=environment,
            )

            self.assertNotEqual(finished.returncode, 0)
            operation = state_root / "operations" / f"{OPERATION_ID}.env"
            self.assertIn("operation_status=pending", operation.read_text(encoding="utf-8"))
            self.assertEqual(
                (state_root / "lock").read_text(encoding="utf-8").strip(),
                OPERATION_ID,
            )
            state = (state_root / "release.env").read_text(encoding="utf-8")
            self.assertIn(f"current={PREVIOUS_SHA}\n", state)

    def test_corrupt_operation_schema_is_rejected_before_any_mutation(self) -> None:
        helper = bash_path(SCRIPTS / "remote-release.sh")
        for action in ("finish", "abort", "status", "begin-deploy"):
            with self.subTest(action=action), tempfile.TemporaryDirectory(
                prefix=f"task5b-schema-{action}-", dir=temporary_parent()
            ) as directory:
                environment, state_root, events, _fake_bin = self.controller_fixture(
                    Path(directory), ssh_fault="none"
                )
                status = "preparing" if action == "begin-deploy" else "pending"
                operation = state_root / "operations" / f"{OPERATION_ID}.env"
                operation.write_text(
                    textwrap.dedent(
                        f"""\
                        operation_schema=1
                        operation_id={OPERATION_ID}
                        operation_status={status}
                        mode=begin-deploy
                        initial_deploy=false
                        target_sha={SHA}
                        target_api_image_reference={API_REFERENCE}
                        target_migration_image_reference={MIGRATION_REFERENCE}
                        target_web_image_reference={WEB_REFERENCE}
                        target_backup_image_reference={BACKUP_REFERENCE}
                        target_rollback_safe=true
                        previous_sha={PREVIOUS_SHA}
                        previous_api_image_reference={PREVIOUS_API_REFERENCE}
                        previous_migration_image_reference={PREVIOUS_MIGRATION_REFERENCE}
                        previous_web_image_reference={PREVIOUS_WEB_REFERENCE}
                        previous_backup_image_reference={PREVIOUS_BACKUP_REFERENCE}
                        previous_rollback_safe=true
                        unsafe_migration_override=false
                        unsafe_migration_reason=
                        outcome=
                        created_at=fixture
                        """
                    ),
                    encoding="utf-8",
                    newline="\n",
                )
                pending = state_root / "pending.env"
                shutil.copyfile(operation, pending)
                lock = state_root / "lock"
                lock.write_text(f"{OPERATION_ID}\n", encoding="utf-8", newline="\n")
                operation_before = operation.read_bytes()
                pending_before = pending.read_bytes()
                state_before = (state_root / "release.env").read_bytes()

                if action == "begin-deploy":
                    arguments = (
                        f"begin-deploy '{OPERATION_ID}' '{SHA}' '{API_REFERENCE}' "
                        f"'{MIGRATION_REFERENCE}' '{WEB_REFERENCE}' '{BACKUP_REFERENCE}' "
                        "true false ''"
                    )
                elif action == "abort":
                    arguments = f"abort '{OPERATION_ID}' operator-cancelled"
                else:
                    arguments = f"{action} '{OPERATION_ID}'"
                result = self.run_bash(
                    f"bash '{helper}' {arguments}",
                    env=environment,
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("delivery operation schema is unsupported", result.stderr)
                self.assertEqual(operation_before, operation.read_bytes())
                self.assertEqual(pending_before, pending.read_bytes())
                self.assertEqual(state_before, (state_root / "release.env").read_bytes())
                self.assertEqual(lock.read_text(encoding="utf-8").strip(), OPERATION_ID)
                self.assertFalse((state_root / "history.log").exists())
                event_text = events.read_text(encoding="utf-8") if events.exists() else ""
                self.assertNotIn("systemctl start", event_text)

    def test_exact_runtime_digest_mismatch_triggers_bounded_restoration(self) -> None:
        script = bash_path(SCRIPTS / "release.sh")
        with tempfile.TemporaryDirectory(
            prefix="task5b-runtime-mismatch-", dir=temporary_parent()
        ) as directory:
            environment, state_root, events, _fake_bin = self.controller_fixture(
                Path(directory), ssh_fault="runtime-digest-mismatch"
            )
            result = self.run_bash(
                f"bash '{script}' deploy '{SHA}'",
                env=environment,
            )

            self.assertNotEqual(result.returncode, 0)
            state = (state_root / "release.env").read_text(encoding="utf-8")
            self.assertIn(f"current={PREVIOUS_SHA}\n", state)
            operation = state_root / "operations" / f"{OPERATION_ID}.env"
            operation_text = operation.read_text(encoding="utf-8")
            self.assertIn("operation_status=aborted", operation_text)
            self.assertIn("outcome=automatic-image-rollback", operation_text)
            self.assertFalse((state_root / "pending.env").exists())
            self.assertFalse((state_root / "lock").exists())
            event_text = events.read_text(encoding="utf-8")
            self.assertEqual(event_text.count(f"release_sha={SHA}"), 1)
            self.assertEqual(event_text.count(f"release_sha={PREVIOUS_SHA}"), 1)
            self.assertIn(f"verify-runtime {SHA} {API_REFERENCE}", event_text)
            self.assertIn(
                f"verify-runtime {PREVIOUS_SHA} {PREVIOUS_API_REFERENCE}",
                event_text,
            )

    def test_remote_helper_rejects_unknown_actions_and_extra_arguments(self) -> None:
        script = bash_path(SCRIPTS / "remote-release.sh")
        for arguments in (
            "unknown-action",
            f"status '{OPERATION_ID}' unexpected",
            f"finish '{OPERATION_ID}' unexpected",
            f"verify-runtime '{SHA}' '{API_REFERENCE}'",
        ):
            with self.subTest(arguments=arguments):
                result = self.run_bash(f"bash '{script}' {arguments}")
                self.assertEqual(result.returncode, 64)

    def test_forced_command_boundary_rejects_arbitrary_ssh_and_forwards_only_helper_argv(self) -> None:
        boundary = bash_path(SCRIPTS / "ssh-command.sh")

        def protocol(arguments: list[str]) -> str:
            payload = b"".join(argument.encode("utf-8") + b"\0" for argument in arguments)
            return f"alright-mate-delivery-v1 {base64.b64encode(payload).decode('ascii')}"

        valid_actions = (
            [
                "begin-deploy",
                OPERATION_ID,
                SHA,
                API_REFERENCE,
                MIGRATION_REFERENCE,
                WEB_REFERENCE,
                BACKUP_REFERENCE,
                "true",
                "false",
                "",
            ],
            ["begin-rollback", OPERATION_ID, "false", ""],
            ["status", OPERATION_ID],
            ["finish", OPERATION_ID],
            ["abort", OPERATION_ID, "operator-cancelled"],
            [
                "verify-runtime",
                SHA,
                API_REFERENCE,
                MIGRATION_REFERENCE,
                WEB_REFERENCE,
                BACKUP_REFERENCE,
            ],
        )

        with tempfile.TemporaryDirectory(
            prefix="task5b-forced-command-", dir=temporary_parent()
        ) as directory:
            fake_bin = Path(directory) / "bin"
            fake_bin.mkdir()
            sudo = fake_bin / "sudo"
            sudo.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env bash
                    [[ ${1:-} == -n && ${2:-} == /usr/local/libexec/alright-mate-delivery ]] || exit 90
                    shift 2
                    printf '%s\\0' "$@"
                    """
                ),
                encoding="utf-8",
                newline="\n",
            )
            sudo.chmod(0o755)
            environment = {"PATH": f"{bash_path(fake_bin)}:{os.environ['PATH']}"}

            for arguments in valid_actions:
                with self.subTest(action=arguments[0]):
                    result = self.run_bash(
                        f"bash '{boundary}'",
                        env={
                            **environment,
                            "SSH_ORIGINAL_COMMAND": protocol(arguments),
                        },
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.split("\0")[:-1], arguments)

            status_token = protocol(["status", OPERATION_ID]).split(" ", 1)[1]
            invalid_commands = (
                "cat /opt/alright-mate/shared/runtime.env",
                f"alright-mate-delivery-v1 {status_token}; cat /etc/passwd",
                "alright-mate-delivery-v1 not-base64!",
                "alright-mate-delivery-v1 "
                + base64.b64encode(b"status").decode("ascii"),
                protocol(["cat", "/opt/alright-mate/shared/runtime.env"]),
            )
            for original_command in invalid_commands:
                with self.subTest(original_command=original_command):
                    result = self.run_bash(
                        f"bash '{boundary}'",
                        env={
                            **environment,
                            "SSH_ORIGINAL_COMMAND": original_command,
                        },
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, "")

    def test_failed_restoration_or_unknown_abort_preserves_reconciliation_state(self) -> None:
        script = bash_path(SCRIPTS / "release.sh")
        for fault in ("restoration-fails", "abort-unknown"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory(
                prefix=f"task5b-reconciliation-{fault}-", dir=temporary_parent()
            ) as directory:
                environment, state_root, events, _fake_bin = self.controller_fixture(
                    Path(directory), ssh_fault=fault
                )
                result = self.run_bash(
                    f"bash '{script}' deploy '{SHA}'",
                    env=environment,
                )

                self.assertNotEqual(result.returncode, 0)
                reconciliation_output = result.stdout + result.stderr
                self.assertIn("delivery_reconciliation=required", reconciliation_output)
                self.assertIn(f"operation={OPERATION_ID}", reconciliation_output)
                self.assertTrue((state_root / "pending.env").is_file())
                self.assertEqual(
                    (state_root / "lock").read_text(encoding="utf-8").strip(),
                    OPERATION_ID,
                )
                operation = state_root / "operations" / f"{OPERATION_ID}.env"
                self.assertIn("operation_status=pending", operation.read_text(encoding="utf-8"))
                state = (state_root / "release.env").read_text(encoding="utf-8")
                self.assertIn(f"current={PREVIOUS_SHA}\n", state)
                event_text = events.read_text(encoding="utf-8")
                self.assertEqual(event_text.count(f"release_sha={SHA}"), 1)
                self.assertEqual(event_text.count(f"release_sha={PREVIOUS_SHA}"), 1)

    def test_remote_kill_before_preflight_keeps_a_replayable_and_abortable_operation(self) -> None:
        helper = bash_path(SCRIPTS / "remote-release.sh")
        with tempfile.TemporaryDirectory(
            prefix="task5b-preflight-crash-", dir=temporary_parent()
        ) as directory:
            environment, state_root, events, _fake_bin = self.controller_fixture(
                Path(directory), ssh_fault="none"
            )

            def begin_command(operation_id: str) -> str:
                return (
                    f"bash '{helper}' begin-deploy '{operation_id}' '{SHA}' "
                    f"'{API_REFERENCE}' '{MIGRATION_REFERENCE}' '{WEB_REFERENCE}' "
                    f"'{BACKUP_REFERENCE}' true false ''"
                )

            replay_operation_id = FAILED_OPERATION_ID
            crashed = self.run_bash(
                f"DELIVERY_CHECK_DISK_CRASH=true {begin_command(replay_operation_id)}",
                env=environment,
            )
            self.assertNotEqual(crashed.returncode, 0)
            replay_operation = state_root / "operations" / f"{replay_operation_id}.env"
            self.assertTrue(replay_operation.is_file())
            replay_operation_text = replay_operation.read_text(encoding="utf-8")
            self.assertIn("operation_schema=2", replay_operation_text)
            self.assertIn("operation_status=preparing", replay_operation_text)
            self.assertIn("preflight_complete=false", replay_operation_text)
            self.assertEqual(
                (state_root / "lock").read_text(encoding="utf-8").strip(),
                replay_operation_id,
            )
            self.assertTrue((state_root / "pending.env").is_file())

            status = self.run_bash(
                f"bash '{helper}' status '{replay_operation_id}'",
                env=environment,
            )
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertIn("operation_status=preparing", status.stdout)

            replayed = self.run_bash(begin_command(replay_operation_id), env=environment)
            self.assertEqual(replayed.returncode, 0, replayed.stderr)
            self.assertIn("operation_status=pending", replayed.stdout)
            self.assertIn(
                "preflight_complete=true",
                replay_operation.read_text(encoding="utf-8"),
            )
            self.assertEqual(
                events.read_text(encoding="utf-8").count("systemctl start alright-mate-backup.service"),
                1,
            )
            replay_abort = self.run_bash(
                f"bash '{helper}' abort '{replay_operation_id}' test-cleanup",
                env=environment,
            )
            self.assertEqual(replay_abort.returncode, 0, replay_abort.stderr)

            abort_operation_id = UNSAFE_OPERATION_ID
            crashed_for_abort = self.run_bash(
                f"DELIVERY_CHECK_DISK_CRASH=true {begin_command(abort_operation_id)}",
                env=environment,
            )
            self.assertNotEqual(crashed_for_abort.returncode, 0)
            abort_operation = state_root / "operations" / f"{abort_operation_id}.env"
            self.assertTrue(abort_operation.is_file())
            aborted = self.run_bash(
                f"bash '{helper}' abort '{abort_operation_id}' operator-cancelled",
                env=environment,
            )
            self.assertEqual(aborted.returncode, 0, aborted.stderr)
            self.assertIn("operation_status=aborted", aborted.stdout)
            self.assertIn(
                "operation_status=aborted",
                abort_operation.read_text(encoding="utf-8"),
            )
            self.assertFalse((state_root / "lock").exists())
            self.assertFalse((state_root / "pending.env").exists())

    def test_remote_state_records_pending_previous_and_atomic_success_after_backup(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="task5b-state-", dir=temporary_parent()
        ) as directory:
            temporary = Path(directory)
            state_root = temporary / "state"
            state_root.mkdir()
            (state_root / "operations").mkdir()
            systemd_root = temporary / "systemd"
            dropin_directory = systemd_root / "alright-mate-backup.service.d"
            dropin_directory.mkdir(parents=True)
            backup_release_environment = temporary / "alright-mate-backup-release.env"
            backup_release_dropin = dropin_directory / "release-environment.conf"
            durable_backup_release_environment = temporary / "durable-backup-release.env"
            runtime_state = temporary / "runtime-state.env"
            install_root = temporary / "install"
            install_root.mkdir()
            runtime_state.write_text(
                f"release_sha={PREVIOUS_SHA}\n"
                f"api_image_reference={PREVIOUS_API_REFERENCE}\n"
                f"migration_image_reference={PREVIOUS_MIGRATION_REFERENCE}\n"
                f"web_image_reference={PREVIOUS_WEB_REFERENCE}\n",
                encoding="utf-8",
                newline="\n",
            )
            durable_backup_release_environment.write_text(
                f"RELEASE_SHA={PREVIOUS_SHA}\nBACKUP_TOOLS_IMAGE={PREVIOUS_BACKUP_REFERENCE}\n",
                encoding="utf-8",
                newline="\n",
            )
            (state_root / "release.env").write_text(
                textwrap.dedent(
                    f"""\
                    current={PREVIOUS_SHA}
                    current_api={PREVIOUS_API_REFERENCE}
                    current_migration={PREVIOUS_MIGRATION_REFERENCE}
                    current_web={PREVIOUS_WEB_REFERENCE}
                    current_backup={PREVIOUS_BACKUP_REFERENCE}
                    current_rollback_safe=true
                    previous={OLDER_SHA}
                    previous_api=ghcr.io/example/alright-mate-api@sha256:{'4' * 64}
                    previous_migration=ghcr.io/example/alright-mate-migration@sha256:{'5' * 64}
                    previous_web=ghcr.io/example/alright-mate-web@sha256:{'6' * 64}
                    previous_backup=ghcr.io/example/alright-mate-backup@sha256:{'8' * 64}
                    previous_rollback_safe=true
                    updated_at=fixture
                    """
                ),
                encoding="utf-8",
                newline="\n",
            )
            fake_bin = temporary / "bin"
            fake_bin.mkdir()
            events = temporary / "events.log"
            fixtures = {
                "df": "#!/bin/sh\nprintf 'Filesystem 1024-blocks Used Available Capacity Mounted on\\nfixture 9999999 1 9999998 1%% /opt/alright-mate\\n'\n",
                "docker": textwrap.dedent(
                    """\
                    #!/bin/sh
                    printf 'docker %s\n' "$*" >>'__EVENTS__'
                    case "$1" in
                      info) ;;
                      ps)
                        case "$*" in
                          *service=api*) echo fixture-api ;;
                          *service=web*) echo fixture-web ;;
                          *service=migrate*) echo fixture-migrate ;;
                          *service=db*) echo fixture-db ;;
                          *service=gateway*) echo fixture-gateway ;;
                          *) exit 10 ;;
                        esac
                        ;;
                      inspect)
                        format=${3:-}
                        container=${4:-}
                        case "$format:$container" in
                          *Config.Image*:*fixture-api) awk -F= '$1 == "api_image_reference" { print substr($0, index($0, "=") + 1) }' "$DELIVERY_FAKE_RUNTIME_STATE" ;;
                          *Config.Image*:*fixture-migrate) awk -F= '$1 == "migration_image_reference" { print substr($0, index($0, "=") + 1) }' "$DELIVERY_FAKE_RUNTIME_STATE" ;;
                          *Config.Image*:*fixture-web) awk -F= '$1 == "web_image_reference" { print substr($0, index($0, "=") + 1) }' "$DELIVERY_FAKE_RUNTIME_STATE" ;;
                          *State.ExitCode*:*fixture-migrate) echo 'exited 0' ;;
                          *State.Health*:*fixture-*) echo 'running healthy' ;;
                          *) exit 11 ;;
                        esac
                        ;;
                      image) [ "${2:-}" = inspect ] ;;
                      *) exit 9 ;;
                    esac
                    """
                ).replace("__EVENTS__", bash_path(events)),
                "readlink": textwrap.dedent(
                    """\
                    #!/bin/sh
                    case "${2:-${1:-}}" in
                      */current)
                        root=${2:-$1}
                        root=${root%/current}
                        sha=$(awk -F= '$1 == "release_sha" { print substr($0, index($0, "=") + 1) }' "$DELIVERY_FAKE_RUNTIME_STATE")
                        printf '%s/releases/%s\n' "$root" "$sha"
                        ;;
                      *) printf '%s\n' "${2:-$1}" ;;
                    esac
                    """
                ),
                "systemctl": textwrap.dedent(
                    f"""\
                    #!/bin/sh
                    printf 'systemctl %s\\n' "$*" >>'{bash_path(events)}'
                    case "$1" in
                      start)
                        test -f "$DELIVERY_BACKUP_RELEASE_ENV_FILE" || exit 18
                        printf 'backup-env-mode=%s\\n' "$(stat -c %a "$DELIVERY_BACKUP_RELEASE_ENV_FILE")" >>'{bash_path(events)}'
                        sed 's/^/backup-env /' "$DELIVERY_BACKUP_RELEASE_ENV_FILE" >>'{bash_path(events)}'
                        dropin="$DELIVERY_SYSTEMD_RUNTIME_ROOT/alright-mate-backup.service.d/release-environment.conf"
                        test -f "$dropin" || exit 19
                        printf 'backup-dropin-mode=%s\\n' "$(stat -c %a "$dropin")" >>'{bash_path(events)}'
                        sed 's/^/backup-dropin /' "$dropin" >>'{bash_path(events)}'
                        if [ "${{DELIVERY_BACKUP_CRASH:-false}}" = true ]; then
                          kill -KILL "$PPID"
                          exit 137
                        fi
                        test "${{DELIVERY_BACKUP_FAIL:-false}}" != true
                        ;;
                      show)
                        case "$*" in *ExecMainStatus*) echo 0;; *) echo success;; esac
                        ;;
                    esac
                    """
                ),
            }
            for name, source in fixtures.items():
                executable = fake_bin / name
                executable.write_text(source, encoding="utf-8", newline="\n")
                executable.chmod(0o755)
            bash_environment = temporary / "bash-env.sh"
            bash_environment.write_text(
                "\n".join(
                    f'{name}() {{ bash "$DELIVERY_FAKE_BIN/{name}" "$@"; }}'
                    for name in fixtures
                )
                + "\n",
                encoding="utf-8",
                newline="\n",
            )

            script = bash_path(SCRIPTS / "remote-release.sh")
            env_prefix = (
                f"PATH='{bash_path(fake_bin)}':$PATH "
                f"BASH_ENV='{bash_path(bash_environment)}' "
                f"DELIVERY_FAKE_BIN='{bash_path(fake_bin)}' "
                f"DELIVERY_STATE_ROOT='{bash_path(state_root)}' "
                f"DELIVERY_SYSTEMD_RUNTIME_ROOT='{bash_path(systemd_root)}' "
                f"DELIVERY_BACKUP_RELEASE_ENV_FILE='{bash_path(backup_release_environment)}' "
                f"DELIVERY_DURABLE_BACKUP_RELEASE_ENV_FILE='{bash_path(durable_backup_release_environment)}' "
                f"DELIVERY_FAKE_RUNTIME_STATE='{bash_path(runtime_state)}' "
                f"DELIVERY_INSTALL_ROOT='{bash_path(install_root)}' "
            )
            begin_command = (
                f"{env_prefix}bash '{script}' begin-deploy '{OPERATION_ID}' '{SHA}' "
                f"'{API_REFERENCE}' '{MIGRATION_REFERENCE}' '{WEB_REFERENCE}' '{BACKUP_REFERENCE}' "
                "true false ''"
            )
            begun = self.run_bash(begin_command)
            self.assertEqual(begun.returncode, 0, f"{begun.stderr}\n{begin_command!r}")
            self.assertIn("operation_status=pending", begun.stdout)
            self.assertIn("operation_schema=2", begun.stdout)
            self.assertIn("unsafe_migration_override=false", begun.stdout)
            self.assertIn("unsafe_migration_reason=", begun.stdout)
            self.assertIn(f"previous_sha={PREVIOUS_SHA}", begun.stdout)
            self.assertIn(f"previous_api_image_reference={PREVIOUS_API_REFERENCE}", begun.stdout)
            self.assertIn(f"previous_backup_image_reference={PREVIOUS_BACKUP_REFERENCE}", begun.stdout)
            self.assertTrue((state_root / "lock").is_file())
            self.assertIn(f"target_sha={SHA}", (state_root / "pending.env").read_text(encoding="utf-8"))
            event_text = events.read_text(encoding="utf-8")
            self.assertIn("systemctl start alright-mate-backup.service", event_text)
            self.assertIn("systemctl show", event_text)
            self.assertIn("backup-env-mode=600", event_text)
            self.assertIn(f"backup-env RELEASE_SHA={PREVIOUS_SHA}", event_text)
            self.assertIn(f"backup-env BACKUP_TOOLS_IMAGE={PREVIOUS_BACKUP_REFERENCE}", event_text)
            self.assertIn("backup-dropin-mode=600", event_text)
            self.assertIn(
                f"backup-dropin EnvironmentFile={bash_path(backup_release_environment)}",
                event_text,
            )
            self.assertFalse(backup_release_environment.exists())
            self.assertFalse(backup_release_dropin.exists())

            repeated = self.run_bash(begin_command)
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            self.assertEqual(repeated.stdout, begun.stdout)
            self.assertEqual(events.read_text(encoding="utf-8").count("systemctl start"), 1)

            rebound = self.run_bash(
                begin_command.replace(BACKUP_REFERENCE, f"ghcr.io/example/alright-mate-backup@sha256:{'f' * 64}")
            )
            self.assertNotEqual(rebound.returncode, 0)
            self.assertIn("operation ID is already bound to another release request", rebound.stderr)
            self.assertEqual(events.read_text(encoding="utf-8").count("systemctl start"), 1)

            status = self.run_bash(f"{env_prefix}bash '{script}' status '{OPERATION_ID}'")
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertIn("operation_status=pending", status.stdout)

            aborted = self.run_bash(
                f"{env_prefix}bash '{script}' abort '{OPERATION_ID}' controller-exit"
            )
            self.assertEqual(aborted.returncode, 0, aborted.stderr)
            self.assertFalse((state_root / "lock").exists())
            self.assertFalse((state_root / "pending.env").exists())
            unchanged = (state_root / "release.env").read_text(encoding="utf-8")
            self.assertIn(f"current={PREVIOUS_SHA}\n", unchanged)

            begun = self.run_bash(begin_command.replace(OPERATION_ID, ROLLBACK_OPERATION_ID))
            self.assertEqual(begun.returncode, 0, begun.stderr)

            runtime_state.write_text(
                f"release_sha={SHA}\n"
                f"api_image_reference={API_REFERENCE}\n"
                f"migration_image_reference={MIGRATION_REFERENCE}\n"
                f"web_image_reference={WEB_REFERENCE}\n",
                encoding="utf-8",
                newline="\n",
            )
            durable_backup_release_environment.write_text(
                f"RELEASE_SHA={SHA}\nBACKUP_TOOLS_IMAGE={BACKUP_REFERENCE}\n",
                encoding="utf-8",
                newline="\n",
            )
            finished = self.run_bash(f"{env_prefix}bash '{script}' finish '{ROLLBACK_OPERATION_ID}'")
            self.assertEqual(
                finished.returncode,
                0,
                f"stdout={finished.stdout}\nstderr={finished.stderr}\nevents={events.read_text(encoding='utf-8')}",
            )
            finished_again = self.run_bash(
                f"{env_prefix}bash '{script}' finish '{ROLLBACK_OPERATION_ID}'"
            )
            self.assertEqual(finished_again.returncode, 0, finished_again.stderr)
            self.assertEqual(finished_again.stdout, finished.stdout)
            state = (state_root / "release.env").read_text(encoding="utf-8")
            self.assertIn(f"current={SHA}\n", state)
            self.assertIn(f"previous={PREVIOUS_SHA}\n", state)
            self.assertIn(f"current_api={API_REFERENCE}\n", state)
            self.assertIn(f"current_migration={MIGRATION_REFERENCE}\n", state)
            self.assertIn(f"current_web={WEB_REFERENCE}\n", state)
            self.assertIn(f"current_backup={BACKUP_REFERENCE}\n", state)
            self.assertIn(f"previous_api={PREVIOUS_API_REFERENCE}\n", state)
            self.assertIn(f"previous_backup={PREVIOUS_BACKUP_REFERENCE}\n", state)
            self.assertFalse((state_root / "lock").exists())
            self.assertFalse((state_root / "pending.env").exists())

            operation = state_root / "operations" / f"{ROLLBACK_OPERATION_ID}.env"
            self.assertIn("operation_status=success", operation.read_text(encoding="utf-8"))
            terminal_status = self.run_bash(
                f"{env_prefix}bash '{script}' status '{ROLLBACK_OPERATION_ID}'"
            )
            self.assertEqual(terminal_status.returncode, 0, terminal_status.stderr)
            self.assertIn("operation_status=success", terminal_status.stdout)

            cannot_abort_success = self.run_bash(
                f"{env_prefix}bash '{script}' abort '{ROLLBACK_OPERATION_ID}' too-late"
            )
            self.assertNotEqual(cannot_abort_success.returncode, 0)

            rollback = self.run_bash(
                f"{env_prefix}bash '{script}' begin-rollback '{MANUAL_ROLLBACK_OPERATION_ID}' false ''"
            )
            self.assertEqual(rollback.returncode, 0, rollback.stderr)
            self.assertIn(f"target_sha={PREVIOUS_SHA}", rollback.stdout)
            self.assertIn(f"target_api_image_reference={PREVIOUS_API_REFERENCE}", rollback.stdout)
            self.assertIn(f"target_backup_image_reference={PREVIOUS_BACKUP_REFERENCE}", rollback.stdout)
            self.assertIn(f"previous_sha={SHA}", rollback.stdout)
            runtime_state.write_text(
                f"release_sha={PREVIOUS_SHA}\n"
                f"api_image_reference={PREVIOUS_API_REFERENCE}\n"
                f"migration_image_reference={PREVIOUS_MIGRATION_REFERENCE}\n"
                f"web_image_reference={PREVIOUS_WEB_REFERENCE}\n",
                encoding="utf-8",
                newline="\n",
            )
            durable_backup_release_environment.write_text(
                f"RELEASE_SHA={PREVIOUS_SHA}\nBACKUP_TOOLS_IMAGE={PREVIOUS_BACKUP_REFERENCE}\n",
                encoding="utf-8",
                newline="\n",
            )
            rollback_finished = self.run_bash(
                f"{env_prefix}bash '{script}' finish '{MANUAL_ROLLBACK_OPERATION_ID}'"
            )
            self.assertEqual(rollback_finished.returncode, 0, rollback_finished.stderr)
            rolled_back_state = (state_root / "release.env").read_text(encoding="utf-8")
            self.assertIn(f"current={PREVIOUS_SHA}\n", rolled_back_state)
            self.assertIn(f"current_api={PREVIOUS_API_REFERENCE}\n", rolled_back_state)
            self.assertIn(f"current_backup={PREVIOUS_BACKUP_REFERENCE}\n", rolled_back_state)
            self.assertIn(f"previous={SHA}\n", rolled_back_state)

            unsafe_reason = "Emergency rollback accepted"
            unsafe_command = (
                f"{env_prefix}bash '{script}' begin-deploy '{UNSAFE_OPERATION_ID}' '{SHA}' "
                f"'{API_REFERENCE}' '{MIGRATION_REFERENCE}' '{WEB_REFERENCE}' '{BACKUP_REFERENCE}' "
                f"false true '{unsafe_reason}'"
            )
            unsafe_begun = self.run_bash(unsafe_command)
            self.assertEqual(unsafe_begun.returncode, 0, unsafe_begun.stderr)
            unsafe_operation = state_root / "operations" / f"{UNSAFE_OPERATION_ID}.env"
            unsafe_operation_text = unsafe_operation.read_text(encoding="utf-8")
            self.assertIn("operation_schema=2", unsafe_operation_text)
            self.assertIn("unsafe_migration_override=true", unsafe_operation_text)
            self.assertIn(f"unsafe_migration_reason={unsafe_reason}", unsafe_operation_text)
            rebound_reason = self.run_bash(unsafe_command.replace("accepted", "reviewed"))
            self.assertNotEqual(rebound_reason.returncode, 0)
            unsafe_aborted = self.run_bash(
                f"{env_prefix}bash '{script}' abort '{UNSAFE_OPERATION_ID}' operator-cancelled"
            )
            self.assertEqual(unsafe_aborted.returncode, 0, unsafe_aborted.stderr)
            history = (state_root / "history.log").read_text(encoding="utf-8")
            self.assertIn("unsafe_migration_override=true", history)
            self.assertIn("unsafe_migration_reason=Emergency\\ rollback\\ accepted", history)

            starts_before_crash = events.read_text(encoding="utf-8").count("systemctl start")
            crashed_command = begin_command.replace(OPERATION_ID, FAILED_OPERATION_ID)
            crashed = self.run_bash(f"DELIVERY_BACKUP_CRASH=true {crashed_command}")
            self.assertNotEqual(crashed.returncode, 0)
            crashed_operation = state_root / "operations" / f"{FAILED_OPERATION_ID}.env"
            crashed_operation_text = crashed_operation.read_text(encoding="utf-8")
            self.assertIn("operation_status=preparing", crashed_operation_text)
            self.assertIn("operation_status=preparing", (state_root / "pending.env").read_text(encoding="utf-8"))
            self.assertEqual(
                (state_root / "lock").read_text(encoding="utf-8").strip(),
                FAILED_OPERATION_ID,
            )

            resumed = self.run_bash(crashed_command)
            self.assertEqual(resumed.returncode, 0, resumed.stderr)
            self.assertIn("operation_status=pending", resumed.stdout)
            self.assertIn(
                "operation_status=pending",
                crashed_operation.read_text(encoding="utf-8"),
            )
            self.assertEqual(
                events.read_text(encoding="utf-8").count("systemctl start"),
                starts_before_crash + 2,
            )
            self.assertFalse(backup_release_environment.exists())
            self.assertFalse(backup_release_dropin.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
