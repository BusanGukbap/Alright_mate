from __future__ import annotations

import json
import os
import re
import subprocess
import unittest
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_COMMAND = [
    "docker",
    "compose",
    "-f",
    "compose.yaml",
    "-f",
    "compose.production.yaml",
    "config",
    "--format",
    "json",
]
RELEASE_SHA = "0123456789abcdef0123456789abcdef01234567"
IMAGE_REFERENCES = {
    "api": "ghcr.io/alright-mate/api@sha256:" + "a" * 64,
    "migrate": "ghcr.io/alright-mate/migration@sha256:" + "b" * 64,
    "web": "ghcr.io/alright-mate/web@sha256:" + "c" * 64,
}
STRICT_DIGEST_REFERENCE = re.compile(
    r"^[a-z0-9.-]+(?::[0-9]+)?/[a-z0-9._/-]+@sha256:[0-9a-f]{64}$"
)
REQUIRED_ENVIRONMENT_EXPRESSION = re.compile(
    r"\$\{([A-Z][A-Z0-9_]*):\?([^}]*)\}"
)
PRODUCTION_INPUT_KEYS = {
    "ACME_WEBROOT_PATH",
    "API_IMAGE",
    "AUTH_GATEWAY_IP",
    "AUTH_SUBNET",
    "BUILD_VERSION",
    "DATABASE_URL",
    "MIGRATION_IMAGE",
    "OAUTH2_PROXY_CLIENT_ID",
    "OAUTH2_PROXY_CLIENT_SECRET",
    "OAUTH2_PROXY_COOKIE_SECRET",
    "OWNER_GITHUB_LOGIN",
    "POSTGRES_DB",
    "POSTGRES_PASSWORD",
    "POSTGRES_USER",
    "RELEASE_SHA",
    "SERVER_NAME",
    "TLS_RUNTIME_DIRECTORY",
    "WEB_IMAGE",
}


def production_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for key in PRODUCTION_INPUT_KEYS:
        environment.pop(key, None)
    environment.update(
        {
            "COMPOSE_DISABLE_ENV_FILE": "1",
            "COMPOSE_PROJECT_NAME": "alright-mate-infra-resolved-test",
            "POSTGRES_DB": "alrightmate",
            "POSTGRES_USER": "alrightmate",
            "POSTGRES_PASSWORD": "fixture-only",
            "DATABASE_URL": (
                "postgres://alrightmate:fixture-only@db:5432/"
                "alrightmate?sslmode=disable"
            ),
            "OWNER_GITHUB_LOGIN": "fixture-owner",
            "BUILD_VERSION": RELEASE_SHA,
            "RELEASE_SHA": RELEASE_SHA,
            "API_IMAGE": IMAGE_REFERENCES["api"],
            "MIGRATION_IMAGE": IMAGE_REFERENCES["migrate"],
            "WEB_IMAGE": IMAGE_REFERENCES["web"],
            "SERVER_NAME": "mate.example.invalid",
            "AUTH_SUBNET": "172.31.255.0/29",
            "AUTH_GATEWAY_IP": "172.31.255.2",
            "TLS_RUNTIME_DIRECTORY": str(PROJECT_ROOT / ".test-runtime" / "tls"),
            "ACME_WEBROOT_PATH": str(PROJECT_ROOT / ".test-runtime" / "acme"),
            "OAUTH2_PROXY_CLIENT_ID": "fixture-client",
            "OAUTH2_PROXY_CLIENT_SECRET": "fixture-client-secret",
            "OAUTH2_PROXY_COOKIE_SECRET": "fixture-cookie-secret",
        }
    )
    return environment


def run_production_config(environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        COMPOSE_COMMAND,
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def required_production_environment() -> dict[str, str]:
    requirements: dict[str, str] = {}
    for compose_path in (PROJECT_ROOT / "compose.yaml", PROJECT_ROOT / "compose.production.yaml"):
        for key, message in REQUIRED_ENVIRONMENT_EXPRESSION.findall(
            compose_path.read_text(encoding="utf-8")
        ):
            if key in requirements and requirements[key] != message:
                raise AssertionError(f"conflicting required-environment messages for {key}")
            requirements[key] = message
    return requirements


class ResolvedProductionComposeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.environment = production_environment()
        result = run_production_config(cls.environment)
        if result.returncode != 0:
            raise AssertionError(
                "production Compose did not render with the strict fixture:\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        cls.config: dict[str, Any] = json.loads(result.stdout)

    def test_application_images_are_immutable_and_preflight_gated(self) -> None:
        services = self.config["services"]
        for service_name, expected_reference in IMAGE_REFERENCES.items():
            with self.subTest(service=service_name):
                service = services[service_name]
                self.assertEqual(expected_reference, service["image"])
                self.assertRegex(service["image"], STRICT_DIGEST_REFERENCE)
                self.assertNotIn("build", service)
                self.assertEqual(
                    "service_completed_successfully",
                    service["depends_on"]["image_reference_preflight"]["condition"],
                )
                self.assertTrue(
                    service["depends_on"]["image_reference_preflight"]["required"]
                )

        preflight = services["image_reference_preflight"]
        self.assertRegex(preflight["image"], r"@sha256:[0-9a-f]{64}$")
        self.assertEqual("none", preflight["network_mode"])
        self.assertTrue(preflight["read_only"])
        self.assertEqual(["ALL"], preflight["cap_drop"])
        self.assertNotIn("ports", preflight)
        self.assertEqual(
            {
                "API_IMAGE": IMAGE_REFERENCES["api"],
                "MIGRATION_IMAGE": IMAGE_REFERENCES["migrate"],
                "WEB_IMAGE": IMAGE_REFERENCES["web"],
            },
            preflight["environment"],
        )
        command = "\n".join(preflight["command"])
        self.assertIn('"$${#digest}" -ne 64', command)
        self.assertIn("*[!0-9a-f]*", command)

    def test_only_gateway_publishes_http_and_https(self) -> None:
        services = self.config["services"]
        publishers = {
            name: service["ports"]
            for name, service in services.items()
            if service.get("ports")
        }
        self.assertEqual({"gateway"}, set(publishers))
        self.assertEqual(
            {
                ("80", 8080, "tcp", "host"),
                ("443", 8443, "tcp", "host"),
            },
            {
                (
                    str(port["published"]),
                    port["target"],
                    port["protocol"],
                    port["mode"],
                )
                for port in publishers["gateway"]
            },
        )

    def test_oauth_is_internal_required_and_probed_for_actual_readiness(self) -> None:
        services = self.config["services"]
        oauth = services["oauth2_proxy"]
        self.assertNotIn("ports", oauth)
        self.assertEqual({"auth"}, set(oauth["networks"]))
        self.assertEqual(["4180"], [str(port) for port in oauth["expose"]])
        self.assertEqual(
            {
                "OAUTH2_PROXY_CLIENT_ID": "fixture-client",
                "OAUTH2_PROXY_CLIENT_SECRET": "fixture-client-secret",
                "OAUTH2_PROXY_COOKIE_NAME": "__Host-alright-mate",
                "OAUTH2_PROXY_COOKIE_SECRET": "fixture-cookie-secret",
            },
            oauth["environment"],
        )
        self.assertIn("--github-user=fixture-owner", oauth["command"])
        self.assertIn(
            "--redirect-url=https://mate.example.invalid/oauth2/callback",
            oauth["command"],
        )
        self.assertNotIn("healthcheck", oauth)

        auth_members = {
            name for name, service in services.items() if "auth" in service.get("networks", {})
        }
        self.assertEqual({"gateway", "oauth2_proxy"}, auth_members)
        self.assertTrue(self.config["networks"]["auth"]["internal"])

        gateway = services["gateway"]
        self.assertEqual(
            "service_started",
            gateway["depends_on"]["oauth2_proxy"]["condition"],
        )
        health_command = " ".join(gateway["healthcheck"]["test"])
        self.assertIn("http://oauth2_proxy:4180/ready", health_command)
        self.assertNotIn("--version", health_command)

    def test_gateway_mounts_tls_and_acme_inputs_read_only(self) -> None:
        volumes = {
            volume["target"]: volume for volume in self.config["services"]["gateway"]["volumes"]
        }
        expected_sources = {
            "/etc/nginx/tls": Path(self.environment["TLS_RUNTIME_DIRECTORY"]).resolve(),
            "/var/www/certbot": Path(self.environment["ACME_WEBROOT_PATH"]).resolve(),
        }
        for target, expected_source in expected_sources.items():
            with self.subTest(target=target):
                volume = volumes[target]
                self.assertEqual("bind", volume["type"])
                self.assertTrue(volume["read_only"])
                self.assertEqual(expected_source, Path(volume["source"]).resolve())

    def test_database_api_and_migration_consume_required_release_inputs(self) -> None:
        services = self.config["services"]
        self.assertEqual(
            {
                "PGDATA": "/var/lib/postgresql/data",
                "POSTGRES_DB": "alrightmate",
                "POSTGRES_USER": "alrightmate",
                "POSTGRES_PASSWORD": "fixture-only",
            },
            services["db"]["environment"],
        )
        self.assertEqual(
            self.environment["DATABASE_URL"],
            services["migrate"]["environment"]["GOOSE_DBSTRING"],
        )
        api_environment = services["api"]["environment"]
        self.assertEqual(self.environment["DATABASE_URL"], api_environment["DATABASE_URL"])
        self.assertEqual("fixture-owner", api_environment["OWNER_GITHUB_LOGIN"])
        self.assertEqual(RELEASE_SHA, self.environment["RELEASE_SHA"])
        self.assertEqual(self.environment["RELEASE_SHA"], api_environment["BUILD_VERSION"])

    def test_missing_required_environment_fails_rendering(self) -> None:
        requirements = required_production_environment()
        self.assertTrue(requirements, "production Compose must declare required inputs")
        self.assertTrue(set(requirements).issubset(PRODUCTION_INPUT_KEYS))
        for key, message in requirements.items():
            with self.subTest(key=key):
                environment = production_environment()
                environment.pop(key)
                result = run_production_config(environment)
                self.assertNotEqual(0, result.returncode, result.stdout)
                self.assertIn(message, result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
