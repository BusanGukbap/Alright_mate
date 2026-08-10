from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OPS = ROOT / "ops"
OVERLAY = ROOT / "compose.observability.yaml"
PRODUCTION = ROOT / "compose.production.yaml"

PUBLIC_RUNTIME_IMAGES = tuple(
    sorted(
        {
            "busybox@sha256:fc6dddc4c44b1bfe37f41cae8e67d1693828e8f42a91862816d7953e2c9d3f23",
            "dxflrs/garage@sha256:866bd13ed2038ba7e7190e840482bc27234c4afaf77be8cfa439ae088c1e4690",
            "cgr.dev/chainguard/postgres@sha256:e67e686bf76586ff0b87fd42553f2dce5e461cb76e090f502ab129aaef141f12",
            "nginx@sha256:ddde39c6e51f02fde7410c2e9c234cf2d0a4c7bdbbe176aeb37d8ad7ab4eb58c",
            "prom/prometheus@sha256:508729e0e2d18e11fd742a5a5ca70e557b940a93948c3c95fd0123a6fd538b69",
            "quay.io/oauth2-proxy/oauth2-proxy-nightly@sha256:522b5685d761526dcae84cc0bef0037475749608a3564a2c9314db563cd8f099",
            "quay.io/prometheus/node-exporter@sha256:1b4e4438faca4dd7e001dd445d161a4a2091b0fededa84093b3a8dfeae1f1be0",
        }
    )
)


def read(relative_path: str) -> str:
    path = ROOT / relative_path
    if not path.is_file():
        raise AssertionError(f"required operational asset is missing: {relative_path}")
    return path.read_text(encoding="utf-8")


class BackupProductionConfigurationTests(unittest.TestCase):
    def test_production_configuration_targets_tofu_managed_regional_aws_s3(self) -> None:
        """Break caught: production operators are directed to Lightsail storage."""
        env = {
            key: value
            for raw_line in read("ops/backup/backup.env.example").splitlines()
            if (line := raw_line.strip())
            and not line.startswith("#")
            and "=" in line
            for key, value in (line.split("=", 1),)
        }
        runbook = read("ops/runbooks/backup-and-restore.md")

        self.assertEqual(
            "https://s3.ap-northeast-2.amazonaws.com", env["S3_ENDPOINT"]
        )
        self.assertEqual("ap-northeast-2", env["S3_REGION"])
        self.assertEqual("replace-with-tofu-backup-bucket-name", env["S3_BUCKET"])
        self.assertEqual("replace-via-secret-store", env["S3_ACCESS_KEY"])
        self.assertEqual("replace-via-secret-store", env["S3_SECRET_KEY"])

        self.assertNotIn("lightsail object-storage", runbook.lower())
        for required_configuration in (
            "AWS S3 bucket",
            "`backup_bucket_name` OpenTofu output",
            "https://s3.ap-northeast-2.amazonaws.com",
            "customer-managed AWS KMS key",
            "does not provision the IAM identity or its credentials",
        ):
            with self.subTest(required_configuration=required_configuration):
                self.assertIn(required_configuration, runbook)


class OperationalComposeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not OVERLAY.is_file():
            raise AssertionError("compose.observability.yaml must be checked in")
        cls.config = cls.render(ROOT / "compose.yaml", OVERLAY)
        cls.production_config = cls.render(ROOT / "compose.yaml", PRODUCTION, OVERLAY)

    @staticmethod
    def render(*files: Path) -> dict:
        command = ["docker", "compose"]
        for file in files:
            command.extend(("-f", str(file)))
        command.extend(("--profile", "*", "config", "--format", "json"))
        env = os.environ.copy()
        env.update(
            {
                "ACME_WEBROOT_PATH": str(ROOT / "tests" / "ops" / "fixtures"),
                "API_IMAGE": "example.invalid/api@sha256:" + "1" * 64,
                "AUTH_GATEWAY_IP": "172.30.0.10",
                "AUTH_SUBNET": "172.30.0.0/24",
                "BUILD_VERSION": "contract-test",
                "DATABASE_URL": "postgres://contract:contract@db:5432/contract?sslmode=disable",
                "GARAGE_RPC_SECRET": "4" * 64,
                "MIGRATION_IMAGE": "example.invalid/migrate@sha256:" + "2" * 64,
                "OAUTH2_PROXY_CLIENT_ID": "contract-test",
                "OAUTH2_PROXY_CLIENT_SECRET": "contract-test",
                "OAUTH2_PROXY_COOKIE_SECRET": "contract-test-contract-test-contract",
                "OWNER_GITHUB_LOGIN": "contract-test",
                "POSTGRES_DB": "contract",
                "POSTGRES_PASSWORD": "contract-test",
                "POSTGRES_USER": "contract",
                "SERVER_NAME": "example.test",
                "TLS_FULLCHAIN_PATH": str(ROOT / "tests" / "ops" / "fixtures" / "not-an-age-identity.txt"),
                "TLS_PRIVATE_KEY_PATH": str(ROOT / "tests" / "ops" / "fixtures" / "not-an-age-identity.txt"),
                "TLS_RUNTIME_DIRECTORY": str(ROOT / "tests" / "ops" / "fixtures"),
                "WEB_IMAGE": "example.invalid/web@sha256:" + "3" * 64,
            }
        )
        completed = subprocess.run(
            command, cwd=ROOT, env=env, check=False, capture_output=True, text=True
        )
        if completed.returncode:
            raise AssertionError(f"Compose config is invalid:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def test_observability_services_are_private_and_digest_pinned(self) -> None:
        services = self.config["services"]
        for name in ("prometheus", "node-exporter"):
            with self.subTest(service=name):
                service = services[name]
                self.assertRegex(service["image"], r"^[^\s]+@sha256:[0-9a-f]{64}$")
                self.assertFalse(service.get("ports"), f"{name} must publish no host ports")
                self.assertIn("observability", service["networks"])
                self.assertTrue(service.get("read_only"))
                self.assertIn("ALL", service.get("cap_drop", []))
                self.assertIn("no-new-privileges:true", service.get("security_opt", []))
                self.assertFalse(service.get("privileged", False))

        self.assertTrue(self.config["networks"]["observability"]["internal"])
        self.assertEqual(
            "prom/prometheus:v3.13.2@sha256:"
            "508729e0e2d18e11fd742a5a5ca70e557b940a93948c3c95fd0123a6fd538b69",
            services["prometheus"]["image"],
        )
        self.assertEqual(
            "quay.io/prometheus/node-exporter:v1.12.1@sha256:"
            "1b4e4438faca4dd7e001dd445d161a4a2091b0fededa84093b3a8dfeae1f1be0",
            services["node-exporter"]["image"],
        )
        self.assertEqual(
            "cgr.dev/chainguard/postgres:latest@sha256:"
            "e67e686bf76586ff0b87fd42553f2dce5e461cb76e090f502ab129aaef141f12",
            services["db"]["image"],
        )
        self.assertEqual(
            "quay.io/oauth2-proxy/oauth2-proxy-nightly:latest@sha256:"
            "522b5685d761526dcae84cc0bef0037475749608a3564a2c9314db563cd8f099",
            self.production_config["services"]["oauth2_proxy"]["image"],
        )

    def test_prometheus_can_scrape_api_without_exposing_api(self) -> None:
        api = self.config["services"]["api"]
        self.assertIn("observability", api["networks"])
        self.assertFalse(api.get("ports"))
        self.assertFalse(self.config["services"]["prometheus"].get("ports"))

    def test_local_object_store_is_private_digest_pinned_garage(self) -> None:
        services = self.config["services"]
        self.assertNotIn("minio", services)
        self.assertNotIn("minio-bootstrap", services)
        garage = services["garage"]
        self.assertIn("backup-test", garage.get("profiles", []))
        self.assertEqual(
            "dxflrs/garage:v2.3.0@sha256:"
            "866bd13ed2038ba7e7190e840482bc27234c4afaf77be8cfa439ae088c1e4690",
            garage["image"],
        )
        self.assertFalse(garage.get("ports"))
        self.assertIn("backup", garage["networks"])
        self.assertTrue(garage.get("read_only"))
        self.assertIn("ALL", garage.get("cap_drop", []))
        self.assertIn("no-new-privileges:true", garage.get("security_opt", []))
        self.assertNotEqual("0:0", garage.get("user"))
        self.assertEqual(
            ["/garage", "server", "--single-node", "--default-bucket"],
            garage["command"],
        )
        self.assertEqual("/etc/garage.toml", garage["environment"]["GARAGE_CONFIG_FILE"])
        config_mount = next(
            volume for volume in garage["volumes"] if volume["target"] == "/etc/garage.toml"
        )
        self.assertTrue(config_mount["read_only"])
        self.assertEqual(
            (OPS / "backup" / "garage.toml").resolve(),
            Path(config_mount["source"]).resolve(),
        )
        self.assertTrue(self.config["networks"]["backup"]["internal"])

        runtime_assets = "\n".join(
            (
                read("compose.observability.yaml"),
                read("ops/security/runtime-image-inventory.json"),
                read("tests/ops/run_local_restore_drill.py"),
            )
        )
        for forbidden in (
            "minio/minio",
            "minio/mc",
            "http://minio:9000",
            "14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e",
            "a7fe349ef4bd8521fb8497f55c6042871b2ae640607cf99d9bede5e9bdf11727",
        ):
            self.assertNotIn(forbidden, runtime_assets)

    def test_backup_and_restore_services_do_not_publish_ports(self) -> None:
        services = self.config["services"]
        for name in ("backup", "restore-drill"):
            with self.subTest(service=name):
                self.assertFalse(services[name].get("ports"))
                self.assertTrue(services[name].get("read_only"))
                self.assertIn("ALL", services[name].get("cap_drop", []))
                self.assertIn("no-new-privileges:true", services[name].get("security_opt", []))

    def test_restore_secret_and_metrics_use_owned_private_volumes(self) -> None:
        services = self.config["services"]

        expected_secret_delivery = {
            "restore-secret-init": ("/private", "restore-secrets", "10001:10001", "0400"),
        }
        for init_name, (target, volume_name, owner, mode) in expected_secret_delivery.items():
            with self.subTest(init=init_name):
                init = services[init_name]
                self.assertEqual("none", init.get("network_mode"))
                self.assertEqual("0:0", init.get("user"))
                self.assertTrue(init.get("read_only"))
                self.assertIn("ALL", init.get("cap_drop", []))
                self.assertEqual(["CHOWN"], init.get("cap_add"))
                self.assertFalse(init.get("ports"))
                volumes = {volume["target"]: volume for volume in init["volumes"]}
                self.assertEqual(volume_name, volumes[target]["source"])
                command = " ".join(init["command"])
                self.assertIn(f"chown {owner}", command)
                self.assertIn(f"chmod {mode}", command)
                self.assertLess(
                    command.index(f"chmod {mode}"), command.index(f"chown {owner}")
                )

        restore = services["restore-drill"]
        restore_volumes = {volume["target"]: volume for volume in restore["volumes"]}
        self.assertNotIn("secrets", restore)
        self.assertEqual("restore-secrets", restore_volumes["/run/private"]["source"])
        self.assertTrue(restore_volumes["/run/private"]["read_only"])
        self.assertEqual(
            "/run/private/age_identity", restore["environment"]["AGE_IDENTITY_FILE"]
        )
        self.assertEqual(
            "service_completed_successfully",
            restore["depends_on"]["restore-secret-init"]["condition"],
        )

        metrics_init = services["backup-metrics-init"]
        self.assertEqual("none", metrics_init.get("network_mode"))
        self.assertEqual("0:0", metrics_init.get("user"))
        self.assertEqual(["CHOWN"], metrics_init.get("cap_add"))
        metrics_command = " ".join(metrics_init["command"])
        self.assertIn("chown 10001:10001 /metrics", metrics_command)
        self.assertIn("chmod 0755 /metrics", metrics_command)
        self.assertLess(
            metrics_command.index("chmod 0755 /metrics"),
            metrics_command.index("chown 10001:10001 /metrics"),
        )
        for service_name, target in (("backup", "/metrics"), ("node-exporter", "/textfile")):
            with self.subTest(service=service_name):
                service = services[service_name]
                volume = next(volume for volume in service["volumes"] if volume["target"] == target)
                self.assertEqual("backup-metrics", volume["source"])
                self.assertEqual(
                    "service_completed_successfully",
                    service["depends_on"]["backup-metrics-init"]["condition"],
                )

    def test_vulnerable_container_telemetry_is_removed_until_patched_upstream(self) -> None:
        services = self.config["services"]
        for removed_service in (
            "cadvisor",
            "docker-socket-proxy",
            "docker-socket-proxy-config-init",
        ):
            self.assertNotIn(removed_service, services)
        self.assertNotIn("docker-control", self.config["networks"])
        self.assertNotIn("docker-proxy-config", self.config["volumes"])

        vulnerable_digest = (
            "e75bdb03b74b0b6995f208f166fead2e6e555dde73e44200113bb26f41b1981d"
        )
        runtime_assets = "\n".join(
            (
                read("compose.observability.yaml"),
                read("ops/security/runtime-image-inventory.json"),
                read("ops/monitoring/prometheus/prometheus.yml"),
                read("ops/monitoring/grafana/dashboards/alright-mate.json"),
            )
        )
        self.assertNotIn(vulnerable_digest, runtime_assets)
        self.assertNotIn("ghcr.io/google/cadvisor", runtime_assets)
        self.assertNotIn("tecnativa/docker-socket-proxy", runtime_assets)
        self.assertNotIn("cadvisor:8080", runtime_assets)
        self.assertNotIn("container_cpu_usage_seconds_total", runtime_assets)
        self.assertNotIn("container_memory_working_set_bytes", runtime_assets)

        degraded_mode = "\n".join(
            (read("ops/runbooks/monitoring-access.md"), read("ops/threat-model.md"))
        ).lower()
        self.assertIn("ghsa-hrxh-6v49-42gf", degraded_mode)
        self.assertIn("grpc-go >= 1.82.1", degraded_mode)
        self.assertIn("container-resource metrics", degraded_mode)
        self.assertIn("pending", degraded_mode)

        for preserved_service in ("prometheus", "node-exporter"):
            self.assertIn(preserved_service, services)
            self.assertFalse(services[preserved_service].get("ports"))
        socket_holders = {
            name
            for name, service in services.items()
            if any(
                volume.get("source") == "/var/run/docker.sock"
                for volume in service.get("volumes", [])
            )
        }
        self.assertEqual({"restore-drill"}, socket_holders)

    def test_vulnerable_grafana_runtime_is_removed_until_clean_official_image(self) -> None:
        services = self.config["services"]
        for removed_service in ("grafana", "grafana-secret-init"):
            self.assertNotIn(removed_service, services)
        for removed_volume in ("grafana-data", "grafana-secrets"):
            self.assertNotIn(removed_volume, self.config["volumes"])

        runtime_assets = "\n".join(
            (
                read("compose.observability.yaml"),
                read("ops/security/runtime-image-inventory.json"),
            )
        )
        for forbidden_runtime_value in (
            "grafana/grafana",
            "GRAFANA_ADMIN_PASSWORD_FILE",
            "./ops/monitoring/grafana",
            "121a7a9ece6dc10b969f1f96eed64b4f07dfac0d0b8abc070f7cb83bbde86f63",
            "ab5cb380e3ff3172d6c8bd2e7cfd31cce977d2881b260e1f5bc089bf0b759b43",
        ):
            self.assertNotIn(forbidden_runtime_value, runtime_assets)

        degraded_mode = "\n".join(
            (
                read("ops/README.md"),
                read("ops/runbooks/monitoring-access.md"),
                read("ops/threat-model.md"),
            )
        ).lower()
        for required_evidence in (
            "grafana 13.1.3",
            "14 high",
            "zero unwaived high/critical",
            "inactive future assets",
            "prometheus ui/api",
        ):
            self.assertIn(required_evidence, degraded_mode)

        prometheus = services["prometheus"]
        self.assertFalse(prometheus.get("ports"))
        self.assertIn("observability", prometheus["networks"])
        self.assertTrue(self.config["networks"]["observability"]["internal"])

    def test_production_publishes_only_the_gateway(self) -> None:
        publishers = {
            name for name, service in self.production_config["services"].items() if service.get("ports")
        }
        self.assertEqual({"gateway"}, publishers)
        self.assertTrue(self.production_config["networks"]["backend"]["internal"])
        for name in ("api", "db", "prometheus", "node-exporter", "garage"):
            self.assertFalse(self.production_config["services"][name].get("ports"), name)


class BackupRestoreAssetTests(unittest.TestCase):
    def test_local_lifecycle_is_not_claimed_as_restore_correctness(self) -> None:
        harness = read("tests/ops/run_local_restore_drill.py")
        active_assets = "\n".join(
            (
                harness,
                read("ops/backup/backup.sh"),
                read("ops/backup/restore-drill.sh"),
                read("compose.observability.yaml"),
            )
        )
        self.assertIn("http://garage:3900", active_assets)
        self.assertNotIn("http://minio:9000", active_assets)
        self.assertNotIn("minio-bootstrap", harness)

        boundary = "\n".join(
            (
                read("ops/README.md"),
                read("ops/runbooks/backup-and-restore.md"),
                read("ops/threat-model.md"),
            )
        ).lower()
        for required in (
            "local elapsed-lifecycle api coverage is not claimed",
            "production retention",
            "encrypted put",
            "copy",
            "completion marker",
            "get",
            "checksum",
            "isolated restore",
        ):
            self.assertIn(required, boundary)

    def test_backup_never_persists_plaintext_and_publishes_completion_last(self) -> None:
        script = read("ops/backup/backup.sh")
        self.assertIn("umask 077", script)
        self.assertRegex(script, r"pg_dump[\s\S]+--format=custom[\s\S]+\|[\s\\]+age")
        self.assertNotRegex(script, r"pg_dump[^\n]+--file(?:=|\s)")
        self.assertIn(".staging", script)
        self.assertIn("_COMPLETE", script)
        self.assertLess(script.index(".staging"), script.rindex("_COMPLETE"))
        self.assertIn("ciphertext_sha256", script)
        self.assertIn("migration_version", script)
        self.assertIn("retention_days", script)

    def test_restore_is_explicit_isolated_verified_and_always_cleaned(self) -> None:
        script = read("ops/backup/restore-drill.sh")
        self.assertIn("umask 077", script)
        self.assertRegex(script, r"backup[_-]id=\"\$\{1:\?", re.IGNORECASE)
        self.assertIn("_COMPLETE", script)
        self.assertIn("sha256sum", script)
        self.assertIn("age --decrypt", script)
        self.assertIn(
            "cgr.dev/chainguard/postgres:latest@sha256:"
            "e67e686bf76586ff0b87fd42553f2dce5e461cb76e090f502ab129aaef141f12",
            script,
        )
        self.assertNotIn("--network host", script)
        docker_run = script[script.index("docker run") : script.index("restore_url=")]
        self.assertNotRegex(docker_run, r"(?:^|\s)-p\s|--publish")
        self.assertIn("pg_restore", script)
        self.assertIn("goose_db_version", script)
        self.assertIn("trap cleanup", script)
        self.assertIn("docker rm -f", script)
        self.assertNotRegex(script, r"docker rm -f[^\n]+\|\| true")
        self.assertIn("docker inspect", script)
        self.assertIn('test ! -e "$workdir"', script)
        self.assertIn("RESTORE_DRILL_TEST_INJECT_CLEANUP_FAILURE", script)
        self.assertIn("cleanup_container_absent=", script)
        self.assertIn("cleanup_workdir_absent=", script)

    def test_backup_metadata_and_restore_require_exact_nonzero_source_counts(self) -> None:
        backup = read("ops/backup/backup.sh")
        restore = read("ops/backup/restore-drill.sh")
        for metadata_field in (
            '"migration_version":',
            '"migration_count":',
            '"expression_count":',
            '"review_event_count":',
        ):
            self.assertIn(metadata_field, backup)
        for expected_value in (
            "expected_migration_version",
            "expected_migration_count",
            "expected_expression_count",
            "expected_review_event_count",
        ):
            self.assertIn(expected_value, restore)
        self.assertIn("migration version does not match encrypted metadata", restore)
        self.assertIn("migration count does not match encrypted metadata", restore)
        self.assertIn("expression count does not match encrypted metadata", restore)
        self.assertIn("review-event count does not match encrypted metadata", restore)

    def test_local_seed_is_dml_for_the_real_migration_schema(self) -> None:
        seed = read("tests/ops/fixtures/seed.sql")
        harness = read("tests/ops/run_local_restore_drill.py")
        self.assertNotIn("CREATE TABLE", seed.upper())
        for real_column in (
            "meaning_ko",
            "status",
            "rating",
            "previous_interval_days",
            "next_interval_days",
        ):
            self.assertIn(real_column, seed)
        self.assertIn('"build", "backup", "migrate"', harness)
        self.assertIn('["run", "--rm", "migrate"]', harness)
        self.assertIn("migrations=1 expressions=1 review_events=1", harness)
        self.assertIn("metadata_migration_mismatch=failure_verified", harness)
        self.assertIn("cleanup_failure=failure_verified", harness)

    def test_backup_toolbox_uses_exact_package_and_base_image_pins(self) -> None:
        dockerfile = read("ops/backup/Dockerfile")
        self.assertIn(
            "FROM alpine:3.24.1@sha256:"
            "28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b",
            dockerfile,
        )
        for package in (
            "postgresql18-client=18.4-r0",
            "age=1.3.1-r5",
            "docker-cli=29.5.3-r0",
            "jq=1.8.1-r0",
            "rclone=1.74.1-r1",
        ):
            self.assertIn(package, dockerfile)
        for forbidden in ("minio-client", "mcli", "/mc"):
            self.assertNotIn(forbidden, dockerfile)

    def test_rclone_transport_is_env_only_and_preserves_atomic_publication(self) -> None:
        backup = read("ops/backup/backup.sh")
        restore = read("ops/backup/restore-drill.sh")
        harness = read("tests/ops/run_local_restore_drill.py")
        compose = read("compose.observability.yaml")

        for script in (backup, restore):
            for setting in (
                'RCLONE_CONFIG="/dev/null"',
                'RCLONE_CONFIG_OPS_TYPE="s3"',
                'RCLONE_CONFIG_OPS_PROVIDER="Other"',
                'RCLONE_CONFIG_OPS_ACCESS_KEY_ID="$S3_ACCESS_KEY"',
                'RCLONE_CONFIG_OPS_SECRET_ACCESS_KEY="$S3_SECRET_KEY"',
                'RCLONE_CONFIG_OPS_ENDPOINT="$S3_ENDPOINT"',
                'RCLONE_CONFIG_OPS_REGION="$S3_REGION"',
                'RCLONE_CONFIG_OPS_FORCE_PATH_STYLE="true"',
            ):
                self.assertIn(setting, script)
            self.assertNotIn("MC_CONFIG", script)
            self.assertNotRegex(script, r"(?:^|\s)mc(?:\s|$)")
            self.assertNotIn("--dump", script)

        self.assertIn('rclone copyto "$dump_ciphertext" "${remote_stage}/database.dump.age"', backup)
        self.assertIn(
            'rclone copyto "${remote_stage}/database.dump.age" "${remote_final}/database.dump.age"',
            backup,
        )
        self.assertIn('rclone rcat "${remote_final}/_COMPLETE"', backup)
        self.assertIn('rclone purge "$remote_stage"', backup)
        self.assertLess(backup.index("${remote_stage}/database.dump.age"), backup.rindex("_COMPLETE"))
        self.assertIn('rclone copyto "${remote_final}/database.dump.age"', restore)
        self.assertIn('"S3_REGION": "garage"', harness)
        self.assertIn("server_side_copy=verified", harness)
        self.assertIn("S3_REGION: ${S3_REGION:-}", compose)

    def test_rclone_endpoint_boundary_remains_fail_closed(self) -> None:
        for path in ("ops/backup/backup.sh", "ops/backup/restore-drill.sh"):
            script = read(path)
            self.assertIn("https://*|http://garage:3900", script)
            self.assertIn("S3_REGION is required", script)
            self.assertNotIn("http://*", script)

    def test_nightly_timer_and_service_are_restricted_and_locked(self) -> None:
        service = read("ops/backup/systemd/alright-mate-backup.service")
        timer = read("ops/backup/systemd/alright-mate-backup.timer")
        for directive in (
            "User=alright-mate-backup",
            "NoNewPrivileges=true",
            "PrivateTmp=true",
            "ProtectSystem=strict",
            "ProtectHome=true",
            "CapabilityBoundingSet=",
            "flock --nonblock",
        ):
            self.assertIn(directive, service)
        self.assertIn("OnCalendar=", timer)
        self.assertIn("RandomizedDelaySec=", timer)
        self.assertIn("Persistent=true", timer)


class MonitoringAssetTests(unittest.TestCase):
    def test_prometheus_scrapes_expected_private_targets(self) -> None:
        config = read("ops/monitoring/prometheus/prometheus.yml")
        for target in ("api:8080", "node-exporter:9100"):
            self.assertIn(target, config)
        self.assertNotIn("cadvisor:8080", config)
        self.assertIn("rule_files:", config)

    def test_rules_cover_service_backup_and_capacity_signals(self) -> None:
        rules = read("ops/monitoring/prometheus/rules/alright-mate.yml")
        for name in (
            "job:alright_mate_http_requests:rate5m",
            "job:alright_mate_http_5xx:ratio5m",
            "job:alright_mate_http_request_duration:p95_5m",
            "AlrightMateUnavailable",
            "AlrightMateHigh5xxRate",
            "AlrightMateHighP95Latency",
            "AlrightMateDiskPressure",
            "AlrightMateBackupFailed",
            "AlrightMateBackupStale",
        ):
            self.assertIn(name, rules)

    def test_inactive_grafana_sources_remain_valid_and_are_not_runtime_claims(self) -> None:
        dashboard = json.loads(read("ops/monitoring/grafana/dashboards/alright-mate.json"))
        self.assertEqual("alright-mate-operations", dashboard["uid"])
        titles = {panel["title"] for panel in dashboard["panels"]}
        required = {
            "Request rate",
            "5xx error ratio",
            "p95 latency",
            "Database readiness",
            "Due reviews",
            "Review events",
            "Host CPU",
            "Host disk available",
        }
        self.assertTrue(required.issubset(titles), required - titles)
        self.assertNotIn("Container CPU", titles)
        self.assertNotIn("Container memory", titles)
        for panel in dashboard["panels"]:
            self.assertTrue(panel.get("targets"), f"{panel['title']} has no query")
            for target in panel["targets"]:
                self.assertNotIn("container_", target.get("expr", ""))
        self.assertIn("inactive future assets", read("ops/README.md").lower())


class HardeningAndDocumentationTests(unittest.TestCase):
    def run_runtime_inventory_validator(
        self,
        inventory: dict,
        *,
        images: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory(prefix="task5c-runtime-inventory-") as directory:
            inventory_path = Path(directory) / "runtime-image-inventory.json"
            inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
            command = [
                sys.executable,
                str(ROOT / "ops" / "security" / "validate_runtime_image_inventory.py"),
                "--repo-root",
                str(ROOT),
                "--inventory",
                str(inventory_path),
            ]
            if images is not None:
                command.extend(("--images", images))
            return subprocess.run(
                command,
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )

    def test_runtime_image_inventory_exactly_covers_live_compose_sources(self) -> None:
        inventory_path = OPS / "security" / "runtime-image-inventory.json"
        validator_path = OPS / "security" / "validate_runtime_image_inventory.py"
        self.assertTrue(inventory_path.is_file())
        self.assertTrue(validator_path.is_file())
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))

        self.assertEqual(
            [
                "compose.yaml",
                "compose.production.yaml",
                "compose.observability.yaml",
                "compose.tls-bootstrap.yaml",
            ],
            inventory["compose_files"],
        )
        self.assertEqual(list(PUBLIC_RUNTIME_IMAGES), inventory["public_images"])
        self.assertEqual(len(PUBLIC_RUNTIME_IMAGES), len(set(inventory["public_images"])))
        self.assertEqual(
            ["API_IMAGE", "MIGRATION_IMAGE", "WEB_IMAGE"],
            inventory["authenticated_application_variables"],
        )
        self.assertEqual(
            ["BACKUP_TOOLS_IMAGE"],
            inventory["authenticated_auxiliary_variables"],
        )
        self.assertEqual(["IMAGE_TAG"], inventory["local_only_variables"])

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
        self.assertEqual(0, printed.returncode, printed.stderr)
        self.assertEqual(" ".join(PUBLIC_RUNTIME_IMAGES), printed.stdout.strip())

        exact = self.run_runtime_inventory_validator(
            inventory, images=" ".join(PUBLIC_RUNTIME_IMAGES)
        )
        self.assertEqual(0, exact.returncode, exact.stderr)

    def test_runtime_image_inventory_rejects_drift_and_unsafe_inputs(self) -> None:
        inventory = json.loads(
            (OPS / "security" / "runtime-image-inventory.json").read_text(encoding="utf-8")
        )
        exact_images = list(PUBLIC_RUNTIME_IMAGES)

        mutations: dict[str, tuple[dict, str | None]] = {
            "missing declaration": (
                {**inventory, "public_images": exact_images[:-1]},
                " ".join(exact_images[:-1]),
            ),
            "stale declaration": (
                {
                    **inventory,
                    "public_images": exact_images
                    + ["example.invalid/stale@sha256:" + "a" * 64],
                },
                " ".join(exact_images),
            ),
            "duplicate declaration": (
                {**inventory, "public_images": exact_images + [exact_images[0]]},
                " ".join(exact_images),
            ),
            "mutable declaration": (
                {**inventory, "public_images": exact_images[:-1] + ["postgres:latest"]},
                " ".join(exact_images),
            ),
            "caller subset": (inventory, " ".join(exact_images[:-1])),
            "caller duplicate": (inventory, " ".join(exact_images + [exact_images[0]])),
            "caller mutable": (inventory, "postgres:latest"),
            "missing authenticated auxiliary": (
                {**inventory, "authenticated_auxiliary_variables": []},
                " ".join(exact_images),
            ),
        }
        for name, (mutated_inventory, images) in mutations.items():
            with self.subTest(case=name):
                rejected = self.run_runtime_inventory_validator(
                    mutated_inventory, images=images
                )
                self.assertNotEqual(0, rejected.returncode, rejected.stdout)

        scan = read("ops/security/scan.sh")
        self.assertIn("validate_runtime_image_inventory.py", scan)
        self.assertIn("runtime-image-inventory.json", scan)
        self.assertIn("config /workspace/apps/api/Dockerfile", scan)
        self.assertIn("config /workspace/apps/web/Dockerfile", scan)

    def test_trivy_policy_fails_on_high_or_critical_findings(self) -> None:
        config = read("ops/security/trivy.yaml")
        self.assertIn("severity:", config)
        self.assertIn("HIGH", config)
        self.assertIn("CRITICAL", config)
        self.assertIn("exit-code: 1", config)
        ignore = json.loads(read("ops/security/.trivyignore.yaml"))
        self.assertEqual([], ignore["vulnerabilities"])
        self.assertEqual([], ignore["misconfigurations"])
        self.assertEqual([], ignore["secrets"])

    def test_patched_prometheus_image_requires_no_temporary_waiver(self) -> None:
        ignore = json.loads(read("ops/security/.trivyignore.yaml"))
        self.assertEqual([], ignore["vulnerabilities"])

    def test_trivy_config_uses_the_v073_nested_scan_schema(self) -> None:
        config = read("ops/security/trivy.yaml")
        self.assertIn(
            "scan:\n"
            "  scanners:\n"
            "    - vuln\n"
            "    - misconfig\n"
            "    - secret\n",
            config,
        )
        self.assertIn("vulnerability:\n  ignore-unfixed: false\n", config)
        self.assertIsNone(
            re.search(r"(?m)^(?:ignore-unfixed|scanners|skip-dirs):", config),
            "Trivy 0.73 silently ignores these scan options at the document root",
        )

    def test_trivy_skips_only_generated_non_source_directories(self) -> None:
        config = read("ops/security/trivy.yaml")
        match = re.search(r"(?m)^  skip-dirs:\n((?:    - .+\n)+)", config)
        self.assertIsNotNone(match, "Trivy must declare the reviewed generated-dir skips")
        skips = {
            line.removeprefix("    - ").strip().strip("\"'")
            for line in match.group(1).splitlines()
        }
        self.assertEqual(
            {
                "/workspace/.Codex",
                "/workspace/.git",
                "/workspace/.tools",
                "/workspace/**/.terraform",
                "/workspace/**/dist",
                "/workspace/**/node_modules",
                "/workspace/playwright-report",
                "/workspace/test-results",
            },
            skips,
        )
        self.assertTrue(all(path.startswith("/workspace/") for path in skips))
        self.assertIn(
            '--volume "${repo_root}:/workspace:ro"',
            read("ops/security/scan.sh"),
        )
        self.assertNotIn("skip-files:", config)
        for protected_source in (
            "apps",
            "deploy",
            "docs",
            "infra",
            "ops",
            "specs",
            "package-lock.json",
            "go.mod",
            "go.sum",
        ):
            self.assertNotIn(protected_source, skips)
        self.assertNotIn("/workspace/.superpowers", skips)

    def test_codex_session_state_is_ignored_non_release_and_has_no_manifest_root(self) -> None:
        git = ["git", "-c", f"safe.directory={ROOT.as_posix()}"]
        ignored = subprocess.run(
            git + ["check-ignore", "--quiet", "--no-index", ".Codex/session-state-probe"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, ignored.returncode, ignored.stderr)
        tracked = subprocess.run(
            git + ["ls-files", "--", ".Codex"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, tracked.returncode, tracked.stderr)
        self.assertEqual("", tracked.stdout.strip(), ".Codex must contain no release-tracked files")

        coverage = json.loads(read("ops/security/dependency-scan-roots.json"))
        declared_paths = [
            value
            for entry in coverage["roots"]
            for value in (entry["path"], *entry["manifests"])
        ]
        self.assertTrue(all(not value.startswith(".Codex/") for value in declared_paths))

        manifest_names = {
            "Pipfile",
            "Pipfile.lock",
            "go.mod",
            "go.sum",
            "npm-shrinkwrap.json",
            "package-lock.json",
            "package.json",
            "poetry.lock",
            "pyproject.toml",
            "uv.lock",
        }
        session_manifests = []
        for directory, _, filenames in os.walk(ROOT / ".Codex"):
            for filename in filenames:
                if filename in manifest_names or (
                    filename.startswith("requirements") and filename.endswith(".txt")
                ):
                    session_manifests.append(
                        (Path(directory).relative_to(ROOT) / filename).as_posix()
                    )
        self.assertEqual([], session_manifests)

        config = read("ops/security/trivy.yaml")
        self.assertIn("    - /workspace/.Codex\n", config)
        self.assertNotIn("/workspace/.superpowers", config)

    def test_dependency_discovery_prunes_codex_but_not_superpowers(self) -> None:
        validator = ROOT / "ops" / "security" / "validate_dependency_coverage.py"
        with tempfile.TemporaryDirectory(prefix="task5c-session-prune-") as directory:
            fixture = Path(directory)
            application = fixture / "apps" / "api"
            application.mkdir(parents=True)
            (application / "go.mod").write_text("module fixture\n\ngo 1.26.0\n", encoding="utf-8")
            session = fixture / ".Codex" / "scratch"
            session.mkdir(parents=True)
            (session / "package.json").write_text('{"private":true}\n', encoding="utf-8")
            coverage = {
                "version": 1,
                "roots": [
                    {
                        "path": "apps/api",
                        "ecosystem": "go",
                        "manifests": ["apps/api/go.mod"],
                        "dependency_scope": "runtime",
                        "trivy_dev_dependencies": "not-applicable",
                        "rationale": "Synthetic release manifest for prune-boundary verification.",
                    }
                ],
            }
            coverage_path = fixture / "coverage.json"
            coverage_path.write_text(json.dumps(coverage), encoding="utf-8")

            accepted = subprocess.run(
                [
                    sys.executable,
                    str(validator),
                    "--repo-root",
                    str(fixture),
                    "--coverage",
                    str(coverage_path),
                    "--print-roots",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(0, accepted.returncode, accepted.stderr)
            self.assertEqual("apps/api", accepted.stdout.strip())

            retained = fixture / ".superpowers" / "sdd"
            retained.mkdir(parents=True)
            (retained / "package.json").write_text('{"private":true}\n', encoding="utf-8")
            rejected = subprocess.run(
                [
                    sys.executable,
                    str(validator),
                    "--repo-root",
                    str(fixture),
                    "--coverage",
                    str(coverage_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertNotEqual(0, rejected.returncode, rejected.stdout)
            self.assertIn(".superpowers/sdd/package.json", rejected.stderr)

    def run_trivy_policy_validator(
        self, policy: dict, images: str
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory(prefix="task5c-trivy-policy-") as directory:
            ignorefile = Path(directory) / ".trivyignore.yaml"
            ignorefile.write_text(json.dumps(policy), encoding="utf-8")
            return subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "ops" / "security" / "validate_trivy_policy.py"),
                    "--ignorefile",
                    str(ignorefile),
                    "--images",
                    images,
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )

    def test_trivy_exception_validator_enforces_bounded_structured_waivers(self) -> None:
        validator_source = read("ops/security/validate_trivy_policy.py")
        self.assertIn("datetime.now(timezone.utc).date()", validator_source)
        self.assertNotIn("date.today()", validator_source)
        utc_today = datetime.now(timezone.utc).date()
        valid_entry = {
            "id": "CVE-2099-0001",
            "paths": ["ops/backup/Dockerfile"],
            "statement": (
                "owner=security; reason=upstream fix pending; "
                "compensating_control=image isolated by read-only root and capability drop"
            ),
            "expired_at": (
                utc_today + timedelta(days=30)
            ).isoformat() + "T00:00:00Z",
        }
        base = {
            "vulnerabilities": [valid_entry],
            "misconfigurations": [],
            "secrets": [],
            "licenses": [],
        }
        digest_image = "registry.example.invalid/alright-mate/backup@sha256:" + "a" * 64
        accepted = self.run_trivy_policy_validator(base, digest_image)
        self.assertEqual(0, accepted.returncode, accepted.stderr)

        invalid_policies = {
            "expiry over 30 days": {
                **base,
                "vulnerabilities": [
                    {
                        **valid_entry,
                        "expired_at": (
                            utc_today + timedelta(days=31)
                        ).isoformat() + "T00:00:00Z",
                    }
                ],
            },
            "date-only expiry": {
                **base,
                "vulnerabilities": [
                    {
                        **valid_entry,
                        "expired_at": (utc_today + timedelta(days=30)).isoformat(),
                    }
                ],
            },
            "missing affected paths": {
                **base,
                "vulnerabilities": [{**valid_entry, "paths": []}],
            },
            "missing owner": {
                **base,
                "vulnerabilities": [
                    {
                        **valid_entry,
                        "statement": "reason=temporary; compensating_control=network isolation",
                    }
                ],
            },
            "missing reason": {
                **base,
                "vulnerabilities": [
                    {
                        **valid_entry,
                        "statement": "owner=security; compensating_control=network isolation",
                    }
                ],
            },
            "missing compensating control": {
                **base,
                "vulnerabilities": [
                    {**valid_entry, "statement": "owner=security; reason=temporary"}
                ],
            },
            "wildcard vulnerability identifier": {
                **base,
                "vulnerabilities": [{**valid_entry, "id": "CVE-2099-*"}],
            },
            "wildcard affected path": {
                **base,
                "vulnerabilities": [{**valid_entry, "paths": ["bin/*"]}],
            },
            "secret exception": {
                **base,
                "vulnerabilities": [],
                "secrets": [{**valid_entry, "id": "synthetic-secret"}],
            },
        }
        for name, policy in invalid_policies.items():
            with self.subTest(case=name):
                rejected = self.run_trivy_policy_validator(policy, digest_image)
                self.assertNotEqual(0, rejected.returncode, rejected.stdout)

    def test_trivy_image_list_is_required_and_digest_only(self) -> None:
        empty_policy = {
            "vulnerabilities": [],
            "misconfigurations": [],
            "secrets": [],
            "licenses": [],
        }
        for invalid_images in (
            "",
            "prom/prometheus:latest",
            "prom/prometheus:v3.13.1",
            "prom/prometheus@sha256:" + "A" * 64,
        ):
            with self.subTest(images=invalid_images):
                rejected = self.run_trivy_policy_validator(empty_policy, invalid_images)
                self.assertNotEqual(0, rejected.returncode, rejected.stdout)

        scan = read("ops/security/scan.sh")
        self.assertIn("validate_trivy_policy.py", scan)
        self.assertIn("--tmpfs /tmp:size=512m,mode=1777", scan)

    def test_trivy_image_stage_has_bounded_space_for_large_immutable_images(self) -> None:
        scan = read("ops/security/scan.sh")
        self.assertRegex(
            scan,
            r"run_trivy_online\(\) \{\s+tmpfs_size=\$1\s+shift[\s\S]+"
            r'--tmpfs "/tmp:size=\$\{tmpfs_size\},mode=1777"[\s\S]+\n\}',
        )
        self.assertRegex(
            scan,
            r"for image in \$images; do\s+"
            r"run_trivy_online 1g image --skip-db-update \"\$image\"\s+done",
        )
        for command in (
            "run_trivy_online 512m fs --download-db-only",
            "run_trivy_online 512m config /workspace/infra",
            "run_trivy_online 512m config /workspace/ops",
            "run_trivy_online 512m config /workspace/apps/api/Dockerfile",
            "run_trivy_online 512m config /workspace/apps/web/Dockerfile",
        ):
            self.assertIn(command, scan)

    def test_dependency_scan_roots_cover_every_repository_manifest(self) -> None:
        coverage_path = OPS / "security" / "dependency-scan-roots.json"
        validator_path = OPS / "security" / "validate_dependency_coverage.py"
        self.assertTrue(coverage_path.is_file(), "dependency scan coverage must be declared")
        self.assertTrue(validator_path.is_file(), "dependency scan coverage must be executable")

        coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
        roots = coverage.get("roots")
        self.assertIsInstance(roots, list)
        self.assertTrue(roots)

        declared_manifests: set[str] = set()
        declared_roots: set[str] = set()
        for entry in roots:
            root = entry["path"]
            manifests = entry["manifests"]
            self.assertNotIn(root, declared_roots, f"duplicate scan root: {root}")
            declared_roots.add(root)
            self.assertIn(entry["ecosystem"], {"go", "npm", "python"})
            self.assertTrue(manifests, f"scan root has no manifests: {root}")
            for manifest in manifests:
                self.assertTrue(
                    manifest == root or manifest.startswith(f"{root}/"),
                    f"{manifest} escapes declared scan root {root}",
                )
                self.assertNotIn(manifest, declared_manifests, f"duplicate manifest: {manifest}")
                declared_manifests.add(manifest)

        ignored_directories = {
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
        discovered_manifests: set[str] = set()
        for directory, child_directories, filenames in os.walk(ROOT):
            child_directories[:] = [
                child for child in child_directories if child not in ignored_directories
            ]
            relative_directory = Path(directory).relative_to(ROOT)
            for filename in filenames:
                is_manifest = filename in {
                    "Pipfile",
                    "Pipfile.lock",
                    "go.mod",
                    "go.sum",
                    "npm-shrinkwrap.json",
                    "package-lock.json",
                    "package.json",
                    "poetry.lock",
                    "pyproject.toml",
                    "uv.lock",
                } or (filename.startswith("requirements") and filename.endswith(".txt"))
                if is_manifest:
                    discovered_manifests.add(
                        (relative_directory / filename).as_posix()
                    )

        self.assertEqual(discovered_manifests, declared_manifests)

        e2e = next(entry for entry in roots if entry["path"] == "tests/e2e")
        self.assertEqual("development", e2e["dependency_scope"])
        self.assertEqual("suppressed", e2e["trivy_dev_dependencies"])
        self.assertTrue(e2e["rationale"].strip())

        valid = subprocess.run(
            [
                sys.executable,
                str(validator_path),
                "--repo-root",
                str(ROOT),
                "--coverage",
                str(coverage_path),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        self.assertEqual(0, valid.returncode, valid.stderr)

        with tempfile.TemporaryDirectory(prefix="task5c-coverage-") as directory:
            incomplete = json.loads(json.dumps(coverage))
            missing = incomplete["roots"][0]["manifests"].pop()
            incomplete_path = Path(directory) / "dependency-scan-roots.json"
            incomplete_path.write_text(json.dumps(incomplete), encoding="utf-8")
            rejected = subprocess.run(
                [
                    sys.executable,
                    str(validator_path),
                    "--repo-root",
                    str(ROOT),
                    "--coverage",
                    str(incomplete_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertNotEqual(0, rejected.returncode, rejected.stdout)
            self.assertIn(missing, rejected.stderr)

    def test_trivy_gate_uses_bounded_manifest_scans_and_separate_workspace_controls(self) -> None:
        scan = read("ops/security/scan.sh")
        self.assertIn("validate_dependency_coverage.py", scan)
        self.assertIn("dependency-scan-roots.json", scan)
        self.assertIn("--scanners vuln", scan)
        self.assertIn("--scanners secret", scan)
        self.assertIn("--skip-db-update", scan)
        self.assertIn("--offline-scan", scan)
        self.assertIn("--timeout 2m", scan)
        self.assertIn("120s", scan)
        self.assertIn("600s", scan)
        self.assertNotIn("--include-dev-deps", scan)
        self.assertNotRegex(scan, r'fs\s+--scanners vuln[^\n]+["\']?/workspace["\']?(?:\s|$)')
        self.assertRegex(scan, r'fs\s+--scanners secret[^\n]+["\']?/workspace["\']?(?:\s|$)')
        self.assertIn("config /workspace/infra", scan)
        self.assertIn("config /workspace/ops", scan)
        self.assertIn("config /workspace/apps/api/Dockerfile", scan)
        self.assertIn("config /workspace/apps/web/Dockerfile", scan)
        self.assertRegex(scan, r'for image in \$images; do[\s\S]+image[^\n]+"\$image"')

    def test_runbooks_cover_required_incidents_and_private_access(self) -> None:
        content = "\n".join(
            (
                read("ops/runbooks/backup-and-restore.md"),
                read("ops/runbooks/monitoring-access.md"),
                read("ops/runbooks/incidents.md"),
                read("ops/threat-model.md"),
            )
        ).lower()
        for topic in (
            "secret rotation",
            "compromised oauth",
            "failed deploy",
            "backup failure",
            "disk pressure",
            "restore drill",
            "ssh tunnel",
        ):
            self.assertIn(topic, content)
        self.assertIn("never exposes prometheus publicly", content)

    def test_repository_contains_no_operational_private_keys(self) -> None:
        if not OPS.exists():
            self.fail("ops directory is missing")
        for path in OPS.rglob("*"):
            if path.is_file():
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("AGE-SECRET-KEY-", text, str(path))
                self.assertNotIn("BEGIN PRIVATE KEY", text, str(path))


if __name__ == "__main__":
    unittest.main(verbosity=2)
