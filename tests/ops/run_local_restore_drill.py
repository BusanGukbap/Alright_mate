from __future__ import annotations

import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "compose.yaml"
OVERLAY = ROOT / "compose.observability.yaml"
SEED = ROOT / "tests" / "ops" / "fixtures" / "seed.sql"
PLACEHOLDER_IDENTITY = ROOT / "tests" / "ops" / "fixtures" / "not-an-age-identity.txt"


def run_completed(
    command: list[str], *, env: dict[str, str], input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        input=input_text,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )


def safe_command_text(command: list[str]) -> str:
    return " ".join(part for part in command if "PASSWORD" not in part and "SECRET" not in part)


def run(command: list[str], *, env: dict[str, str], input_text: str | None = None) -> str:
    completed = run_completed(command, env=env, input_text=input_text)
    if completed.returncode:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {safe_command_text(command)}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed.stdout


def run_expect_failure(command: list[str], *, env: dict[str, str]) -> str:
    completed = run_completed(command, env=env)
    if completed.returncode == 0:
        raise RuntimeError(
            f"command unexpectedly succeeded: {safe_command_text(command)}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed.stdout + completed.stderr


def assert_no_restore_container(backup_id: str, *, env: dict[str, str]) -> None:
    prefix = f"alright-mate-restore-{backup_id[:32]}"
    container_ids = run(
        ["docker", "ps", "--all", "--quiet", "--filter", f"name={prefix}"], env=env
    ).strip()
    if container_ids:
        raise RuntimeError(f"restore cleanup left disposable containers: {container_ids}")


def assert_private_volume_file(
    image: str,
    volume: str,
    path: str,
    expected_stat: str,
    run_as: str,
    *,
    env: dict[str, str],
) -> None:
    run(
        [
            "docker",
            "run",
            "--rm",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--user",
            run_as,
            "--volume",
            f"{volume}:/private:ro",
            "--entrypoint",
            "/bin/sh",
            image,
            "-euc",
            f'test "$(stat -c \'%u:%g:%a\' {path})" = {expected_stat}; test -r {path}',
        ],
        env=env,
    )


def wait_for_healthy_compose_service(
    compose: list[str], service: str, *, env: dict[str, str]
) -> None:
    container = run(compose + ["ps", "--quiet", service], env=env).strip()
    if not container:
        raise RuntimeError(f"{service} container was not created")
    for _ in range(60):
        ready = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Health.Status}}", container],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if ready.returncode == 0 and ready.stdout.strip() == "healthy":
            return
        time.sleep(1)
    raise RuntimeError(f"{service} did not become ready")


def main() -> int:
    suffix = f"{os.getpid()}-{secrets.token_hex(3)}"
    project = f"alright-mate-task5c-{suffix}"
    image = f"alright-mate/backup-tools:{suffix}"
    migration_tag = f"task5c-{suffix}"
    migration_image = f"alright-mate-migration:{migration_tag}"
    temporary = tempfile.TemporaryDirectory(prefix=f"{project}-")
    temporary_path = Path(temporary.name)
    identity_path = temporary_path / "age-identity.txt"
    access_key = "GK" + secrets.token_hex(16)
    secret_key = secrets.token_hex(32)

    env = os.environ.copy()
    env.update(
        {
            "AGE_IDENTITY_FILE": str(identity_path),
            "BACKUP_RETENTION_DAYS": "2",
            "BACKUP_TOOLS_IMAGE": image,
            "COMPOSE_PROJECT_NAME": project,
            "DATABASE_URL": "postgres://alrightmate:local-development-only@db:5432/alrightmate?sslmode=disable",
            "DOCKER_GID": "0",
            "GARAGE_RPC_SECRET": secrets.token_hex(32),
            "IMAGE_TAG": migration_tag,
            "RELEASE_SHA": "0123456789abcdef0123456789abcdef01234567",
            "RESTORE_NETWORK": f"{project}_backend",
            "S3_ACCESS_KEY": access_key,
            "S3_BUCKET": "alright-mate-drill",
            "S3_ENDPOINT": "http://garage:3900",
            "S3_REGION": "garage",
            "S3_SECRET_KEY": secret_key,
        }
    )
    compose = ["docker", "compose", "-f", str(BASE), "-f", str(OVERLAY), "--project-name", project]
    backup_id = ""
    success_evidence = ""
    workflow_error: BaseException | None = None
    cleanup_errors: list[str] = []
    try:
        run(compose + ["build", "backup", "migrate"], env=env)
        identity_text = run(
            [
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "age-keygen",
                image,
            ],
            env=env,
        )
        identity_path.write_text(identity_text, encoding="utf-8")
        recipient = run(
            [
                "docker",
                "run",
                "--rm",
                "--mount",
                f"type=bind,source={identity_path},target=/source/identity.txt,readonly",
                "--entrypoint",
                "age-keygen",
                image,
                "-y",
                "/source/identity.txt",
            ],
            env=env,
        ).strip()
        if not recipient.startswith("age1"):
            raise RuntimeError("age-keygen did not return a public recipient")
        env["AGE_RECIPIENT"] = recipient

        run(compose + ["--profile", "backup-test", "up", "--detach", "db", "garage"], env=env)
        wait_for_healthy_compose_service(compose, "db", env=env)
        wait_for_healthy_compose_service(compose, "garage", env=env)

        run(compose + ["run", "--rm", "migrate"], env=env)
        run(
            compose + ["exec", "-T", "db", "psql", "-v", "ON_ERROR_STOP=1", "-U", "alrightmate", "-d", "alrightmate"],
            env=env,
            input_text=SEED.read_text(encoding="utf-8"),
        )
        source_counts = run(
            compose
            + [
                "exec",
                "-T",
                "db",
                "psql",
                "--no-psqlrc",
                "--tuples-only",
                "--no-align",
                "--username",
                "alrightmate",
                "--dbname",
                "alrightmate",
                "--command",
                "SELECT COALESCE(MAX(version_id), 0), "
                "COUNT(*) FILTER (WHERE is_applied AND version_id > 0), "
                "(SELECT COUNT(*) FROM expressions), "
                "(SELECT COUNT(*) FROM review_events) FROM goose_db_version",
            ],
            env=env,
        ).strip()
        if source_counts != "1|1|1|1":
            raise RuntimeError(f"real migration seed counts are not exact: {source_counts}")

        run(compose + ["--profile", "operations", "run", "--rm", "backup-metrics-init"], env=env)
        backup_completed = run_completed(
            compose
            + ["--profile", "operations", "run", "--rm", "--no-deps", "backup"],
            env=env,
        )
        if backup_completed.returncode:
            raise RuntimeError(
                "backup failed without exposing credentials:\n"
                f"stdout:\n{backup_completed.stdout}\nstderr:\n{backup_completed.stderr}"
            )
        backup_output = backup_completed.stdout + backup_completed.stderr
        match = re.search(r"backup_result=success backup_id=([0-9A-Za-z._-]+) duration_seconds=(\d+)", backup_output)
        if not match:
            raise RuntimeError(f"backup did not report success:\n{backup_output}")
        if len(re.findall(r"Copied \(server-side copy\)", backup_output)) < 2:
            raise RuntimeError(
                "rclone did not report both same-remote publication copies as server-side:\n"
                + backup_output
            )
        backup_id = match.group(1)

        metrics_volume = f"{project}_backup-metrics"
        run(
            [
                "docker",
                "run",
                "--rm",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--user",
                "10001:10001",
                "--volume",
                f"{metrics_volume}:/metrics:ro",
                "--entrypoint",
                "/bin/sh",
                image,
                "-euc",
                "test \"$(stat -c '%u:%g:%a' /metrics)\" = 10001:10001:755; "
                "for metric in alright_mate_backup_last_run_success alright_mate_backup_last_success_timestamp_seconds alright_mate_backup_last_attempt_timestamp_seconds; do "
                "test \"$(stat -c '%u:%g:%a' /metrics/${metric}.prom)\" = 10001:10001:644; "
                "grep -q \"^${metric} \" /metrics/${metric}.prom; done",
            ],
            env=env,
        )

        run(
            compose + ["--profile", "restore", "run", "--rm", "restore-secret-init"],
            env=env,
        )
        assert_private_volume_file(
            image,
            f"{project}_restore-secrets",
            "/private/age_identity",
            "10001:10001:400",
            "10001:10001",
            env=env,
        )

        restore_output = run(
            compose
            + [
                "--profile",
                "restore",
                "run",
                "--rm",
                "--no-deps",
                "restore-drill",
                backup_id,
            ],
            env=env,
        )
        smoke = re.search(
            r"restore_smoke=verified migration_version=(\d+) migrations=(\d+) expressions=(\d+) review_events=(\d+) ciphertext_sha256=([0-9a-f]{64})",
            restore_output,
        )
        result = re.search(
            r"restore_drill_result=success backup_id=[^ ]+ duration_seconds=(\d+) cleanup_container_absent=1 cleanup_workdir_absent=1",
            restore_output,
        )
        if not smoke or not result:
            raise RuntimeError(f"restore did not report verified success:\n{restore_output}")
        if smoke.groups()[:4] != ("1", "1", "1", "1"):
            raise RuntimeError(f"restore counts are not exact and non-zero: {smoke.groups()[:4]}")
        assert_no_restore_container(backup_id, env=env)

        cleanup_failure_output = run_expect_failure(
            compose
            + [
                "--profile",
                "restore",
                "run",
                "--rm",
                "--no-deps",
                "--env",
                "RESTORE_DRILL_TEST_INJECT_CLEANUP_FAILURE=workdir",
                "restore-drill",
                backup_id,
            ],
            env=env,
        )
        if not re.search(
            r"restore_drill_result=failure .*cleanup_container_absent=1 cleanup_workdir_absent=0",
            cleanup_failure_output,
        ):
            raise RuntimeError(
                "cleanup failure injection did not fail the final result:\n"
                + cleanup_failure_output
            )
        assert_no_restore_container(backup_id, env=env)

        mutation_script = """
set -eu
work=$(mktemp -d /work/metadata-mismatch.XXXXXX)
trap 'rm -rf -- "$work"' EXIT
export RCLONE_CONFIG="/dev/null"
export RCLONE_CONFIG_OPS_TYPE="s3"
export RCLONE_CONFIG_OPS_PROVIDER="Other"
export RCLONE_CONFIG_OPS_ACCESS_KEY_ID="$S3_ACCESS_KEY"
export RCLONE_CONFIG_OPS_SECRET_ACCESS_KEY="$S3_SECRET_KEY"
export RCLONE_CONFIG_OPS_ENDPOINT="$S3_ENDPOINT"
export RCLONE_CONFIG_OPS_REGION="$S3_REGION"
export RCLONE_CONFIG_OPS_FORCE_PATH_STYLE="true"
object="ops:${S3_BUCKET}/backups/$BACKUP_ID"
rclone copyto "${object}/metadata.json.age" "$work/metadata.json.age"
age --decrypt --identity /run/private/age_identity --output "$work/metadata.json" "$work/metadata.json.age"
jq '.migration_version += 1' "$work/metadata.json" >"$work/metadata-mutated.json"
age --encrypt --recipient "$AGE_RECIPIENT" --output "$work/metadata-mutated.json.age" "$work/metadata-mutated.json"
rclone copyto "$work/metadata-mutated.json.age" "${object}/metadata.json.age"
"""
        run(
            compose
            + [
                "--profile",
                "restore",
                "run",
                "--rm",
                "--no-deps",
                "--entrypoint",
                "/bin/sh",
                "--env",
                f"AGE_RECIPIENT={recipient}",
                "--env",
                f"BACKUP_ID={backup_id}",
                "restore-drill",
                "-euc",
                mutation_script,
            ],
            env=env,
        )
        metadata_failure_output = run_expect_failure(
            compose
            + [
                "--profile",
                "restore",
                "run",
                "--rm",
                "--no-deps",
                "restore-drill",
                backup_id,
            ],
            env=env,
        )
        if "migration version does not match encrypted metadata" not in metadata_failure_output:
            raise RuntimeError(
                "metadata migration mismatch was not rejected:\n" + metadata_failure_output
            )
        if not re.search(
            r"restore_drill_result=failure .*cleanup_container_absent=1 cleanup_workdir_absent=1",
            metadata_failure_output,
        ):
            raise RuntimeError(
                "metadata mismatch did not prove cleanup:\n" + metadata_failure_output
            )
        assert_no_restore_container(backup_id, env=env)

        success_evidence = (
            "local_restore_drill=success "
            "object_store=garage-v2.3.0 local_lifecycle_api=not_claimed "
            f"backup_id={backup_id} "
            f"ciphertext_sha256={smoke.group(5)} "
            f"migration_version={smoke.group(1)} "
            "migrations=1 expressions=1 review_events=1 "
            "server_side_copy=verified "
            "cleanup_failure=failure_verified "
            "metadata_migration_mismatch=failure_verified "
            f"duration_seconds={result.group(1)}"
        )
    except BaseException as error:
        workflow_error = error
    finally:
        if project.startswith("alright-mate-task5c-"):
            down = run_completed(
                compose + ["--profile", "*", "down", "--volumes", "--remove-orphans"],
                env=env,
            )
            if down.returncode:
                cleanup_errors.append(
                    f"Compose cleanup failed ({down.returncode}): {down.stderr.strip()}"
                )
        temporary.cleanup()
        if temporary_path.exists():
            cleanup_errors.append(f"plaintext temporary directory remains: {temporary_path}")

        if backup_id:
            prefix = f"alright-mate-restore-{backup_id[:32]}"
            remaining_restore = run_completed(
                ["docker", "ps", "--all", "--quiet", "--filter", f"name={prefix}"],
                env=env,
            )
            if remaining_restore.returncode or remaining_restore.stdout.strip():
                cleanup_errors.append(
                    "disposable restore container remains: "
                    + (remaining_restore.stdout.strip() or remaining_restore.stderr.strip())
                )

        for resource, command in (
            (
                "Compose container",
                [
                    "docker",
                    "ps",
                    "--all",
                    "--quiet",
                    "--filter",
                    f"label=com.docker.compose.project={project}",
                ],
            ),
            (
                "Compose volume",
                [
                    "docker",
                    "volume",
                    "ls",
                    "--quiet",
                    "--filter",
                    f"label=com.docker.compose.project={project}",
                ],
            ),
        ):
            remaining = run_completed(command, env=env)
            if remaining.returncode or remaining.stdout.strip():
                cleanup_errors.append(
                    f"{resource} cleanup proof failed: "
                    + (remaining.stdout.strip() or remaining.stderr.strip())
                )

        for generated_image in (image, migration_image):
            if generated_image.startswith(("alright-mate/backup-tools:", "alright-mate-migration:task5c-")):
                removal = run_completed(["docker", "image", "rm", generated_image], env=env)
                if removal.returncode and "No such image" not in removal.stderr:
                    cleanup_errors.append(
                        f"generated image cleanup failed for {generated_image}: {removal.stderr.strip()}"
                    )

    if cleanup_errors:
        detail = "; ".join(cleanup_errors)
        if workflow_error is not None:
            raise RuntimeError(f"workflow failed: {workflow_error}; cleanup failed: {detail}")
        raise RuntimeError(f"cleanup failed: {detail}")
    if workflow_error is not None:
        raise workflow_error
    if not success_evidence:
        raise RuntimeError("restore drill produced no success evidence")
    print(success_evidence + " cleanup_verified=1")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"local_restore_drill=failure error={error}", file=sys.stderr)
        raise SystemExit(1)
