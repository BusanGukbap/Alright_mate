from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def target_header(makefile: str, target: str) -> str:
    match = re.search(rf"(?m)^{re.escape(target)}\s*:(?P<dependencies>[^\n]*)$", makefile)
    if match is None:
        raise AssertionError(f"Make target is missing: {target}")
    return match.group("dependencies")


def target_recipe(makefile: str, target: str) -> str:
    match = re.search(
        rf"(?ms)^{re.escape(target)}\s*:[^\n]*\n(?P<recipe>(?:\t[^\n]*\n|\n)*)",
        makefile,
    )
    if match is None:
        raise AssertionError(f"Make recipe is missing: {target}")
    return match.group("recipe")


class RootCommandContract(unittest.TestCase):
    def test_root_interface_keeps_expensive_or_mutating_work_explicit(self) -> None:
        """Break caught: a required command disappears or `check` starts external work."""

        makefile = read("Makefile")
        required = {
            "dev",
            "generate",
            "openapi-lint",
            "check",
            "e2e",
            "infra-check",
            "security-check",
            "delivery-lint",
            "deploy",
            "rollback",
            "backup",
            "restore-drill",
        }
        for target in required:
            target_header(makefile, target)

        check_surface = target_header(makefile, "check") + target_recipe(makefile, "check")
        for required_check in (
            "generate-check",
            "openapi-lint",
            "portfolio-test",
            "backend-check",
            "frontend-check",
            "runtime-check",
            "delivery-test",
            "docs-spellcheck",
        ):
            self.assertIn(required_check, check_surface)
        for forbidden_check in (
            "e2e",
            "infra-check",
            "security-check",
            "deploy",
            "rollback",
            "backup",
            "restore-drill",
        ):
            self.assertNotIn(forbidden_check, check_surface)

    def test_every_docker_integration_entry_point_fails_closed(self) -> None:
        """Break caught: a Docker suite can skip its daemon preflight and report success."""

        makefile = read("Makefile")
        docker_guard = target_recipe(makefile, "require-docker")
        self.assertRegex(docker_guard, r"\$\(DOCKER\)\s+info")
        self.assertNotRegex(docker_guard, r"\|\|\s*true")
        self.assertRegex(docker_guard, r"Docker.+required")

        for target in ("dev", "check", "e2e", "security-check", "backup", "restore-drill"):
            dependencies = target_header(makefile, target).split()
            self.assertIn("require-docker", dependencies, target)

        backend_recipe = target_recipe(makefile, "backend-check")
        self.assertIn("REQUIRE_DOCKER=1", backend_recipe)

    def test_production_commands_only_dispatch_protected_main_workflows(self) -> None:
        """Break caught: a public Make target bypasses GitHub Environment approval."""

        makefile = read("Makefile")
        deploy_recipe = target_recipe(makefile, "deploy")
        rollback_recipe = target_recipe(makefile, "rollback")

        self.assertIn("gh workflow run deploy.yml", deploy_recipe)
        self.assertIn("--ref main", deploy_recipe)
        self.assertIn("release_sha=", deploy_recipe)
        self.assertIn("$(RELEASE_SHA)", deploy_recipe)
        self.assertIn("gh workflow run rollback.yml", rollback_recipe)
        self.assertIn("--ref main", rollback_recipe)
        self.assertNotIn("scripts/delivery/release.sh", deploy_recipe + rollback_recipe)

    def test_security_check_derives_the_exact_runtime_inventory_in_one_shell(self) -> None:
        """Break caught: the public scanner starts without its fail-closed image inventory."""

        recipe = target_recipe(read("Makefile"), "security-check")
        self.assertIn("validate_runtime_image_inventory.py", recipe)
        self.assertIn("--print-public-images", recipe)
        self.assertIn("TRIVY_IMAGES", recipe)
        self.assertIn("ops/security/scan.sh", recipe)
        self.assertRegex(recipe, r"images=.*validate_runtime_image_inventory")
        self.assertNotIn("@sha256:", recipe)

    def test_docs_spellcheck_passes_options_to_cspell_before_file_arguments(self) -> None:
        """Break caught: a second separator makes cspell treat its flags as filenames."""

        recipe = target_recipe(read("Makefile"), "docs-spellcheck")
        self.assertRegex(recipe, r"exec\s+--\s+cspell\s+--config")
        self.assertNotRegex(recipe, r"cspell\s+--\s+--config")

    def test_openapi_lint_matches_the_pinned_ci_contract(self) -> None:
        """Break caught: local checks omit the OpenAPI linter used by CI."""

        recipe = target_recipe(read("Makefile"), "openapi-lint")
        self.assertIn("@redocly/cli@2.43.3", recipe)
        self.assertIn("redocly lint specs/openapi.yaml", recipe)


class PortfolioDocumentationContract(unittest.TestCase):
    def test_readme_is_a_clean_clone_entry_point_without_invented_production_evidence(self) -> None:
        """Break caught: onboarding omits a root command or blurs local and production status."""

        readme = read("README.md")
        for command in (
            "make dev",
            "make generate",
            "make openapi-lint",
            "make check",
            "make e2e",
            "make infra-check",
            "make delivery-lint",
            "make deploy RELEASE_SHA=<sha>",
            "make rollback",
            "make backup",
            "make restore-drill",
        ):
            self.assertIn(f"`{command}`", readme)

        self.assertIn("http://localhost:8080", readme)
        self.assertRegex(readme, r"(?i)gateway.+only.+host port")
        self.assertRegex(readme, r"(?i)verified locally")
        self.assertRegex(readme, r"(?i)pending owner-authorised work")
        for pending_boundary in (
            "AWS apply",
            "DNS delegation",
            "TLS issuance",
            "GitHub OAuth",
            "GHCR publication",
            "production deployment",
            "production rollback",
        ):
            self.assertIn(pending_boundary, readme)
        self.assertNotRegex(readme, r"https://(?:www\.)?alright[-.]mate\.[a-z]{2,}")
        self.assertRegex(readme, r"(?i)required reviewer.+pending|pending.+required reviewer")

    def test_delivery_handoff_describes_eligibility_digests_and_idempotent_operations(self) -> None:
        """Break caught: portfolio prose reduces immutable delivery to a mutable SHA tag."""

        readme = read("README.md")
        portfolio = read("docs/portfolio.md")
        decision_log = read("docs/ai/decision-log.md")

        self.assertIn("workflow_dispatch", readme)
        self.assertRegex(readme, r"(?i)authenticated.+GitHub CLI")
        for document in (readme, portfolio, decision_log):
            self.assertRegex(document, r"(?i)eligibility manifest")
            self.assertRegex(document, r"(?i)exact.+API.+migration.+web.+digests")
            self.assertRegex(document, r"(?i)idempotent.+operation")
            self.assertRegex(document, r"(?i)backup[- ]tools")

    def test_observability_and_security_claims_match_the_verified_runtime(self) -> None:
        """Break caught: portfolio prose claims removed vulnerable monitoring services."""

        readme = read("README.md")
        portfolio = read("docs/portfolio.md")
        decision_log = read("docs/ai/decision-log.md")

        self.assertNotIn("Prometheus and Grafana assets", readme)
        self.assertNotRegex(portfolio, r"node_exporter\s*/\s*cAdvisor")
        self.assertNotIn("Docker socket is placed behind an allow-listed proxy", portfolio)
        self.assertNotIn("Monitoring uses a restricted Docker socket proxy", decision_log)
        for document in (readme, portfolio, decision_log):
            self.assertRegex(document, r"(?i)Prometheus")
            self.assertRegex(document, r"(?i)Grafana.+deferred|deferred.+Grafana")
            self.assertRegex(document, r"(?i)cAdvisor.+deferred|deferred.+cAdvisor")
        self.assertNotIn("TASK5C_FINAL_STATUS", portfolio)
        self.assertRegex(portfolio, r"(?i)bounded.+aggregate.+zero.+HIGH.+CRITICAL")

    def test_portfolio_traces_each_user_journey_to_specification_proof_and_boundary(self) -> None:
        """Break caught: a reviewer cannot follow a feature from intent to automated evidence."""

        portfolio = read("docs/portfolio.md")
        expected_trace_links = {
            "public browse": (
                "docs/specs/000-product.md",
                "docs/specs/001-public-expressions.md",
                "tests/e2e/tests/public.spec.ts",
                "apps/web/src/routes/ExpressionsPage.tsx",
            ),
            "owner CRUD": (
                "docs/specs/002-owner-admin.md",
                "tests/e2e/tests/owner.spec.ts",
                "apps/web/src/routes/AdminPage.tsx",
                "apps/api/internal/application/expressions.go",
            ),
            "review scheduling": (
                "docs/specs/003-review.md",
                "tests/e2e/tests/review.spec.ts",
                "apps/api/internal/review/schedule.go",
                "apps/web/src/routes/ReviewPage.tsx",
            ),
        }
        lowered = portfolio.lower()
        for journey, paths in expected_trace_links.items():
            self.assertIn(journey.lower(), lowered)
            for path in paths:
                self.assertIn(path, portfolio, f"{journey}: {path}")

        self.assertRegex(portfolio, r"Browser.+Nginx.+oauth2-proxy.+Go API.+PostgreSQL")
        self.assertIn("Asia/Seoul", portfolio)
        self.assertIn("RFC 9457", portfolio)
        self.assertIn("Kubernetes", portfolio)

    def test_ai_log_records_role_separation_decisions_and_human_corrections(self) -> None:
        """Break caught: the AI log conflates executable evidence with human decisions."""

        decision_log = read("docs/ai/decision-log.md")
        for role in (
            "Spec drafter",
            "Test designer",
            "Implementer",
            "Reviewer",
            "British English tutor",
            "Operations reviewer",
        ):
            self.assertIn(role, decision_log)
        for decision_state in (
            "Adopted",
            "Rejected",
            "Executable verification",
            "Human decision",
            "Review correction",
        ):
            self.assertIn(decision_state, decision_log)
        self.assertNotIn("Human verification", decision_log)
        self.assertRegex(decision_log, r"(?i)no raw transcripts")

    def test_cost_handoff_separates_s3_usage_from_kms_key_material(self) -> None:
        """Break caught: the fixed KMS charge is mislabelled as object-storage cost."""

        portfolio = read("docs/portfolio.md")
        self.assertNotIn("roughly USD 1 for object storage", portfolio)
        self.assertRegex(portfolio, r"(?i)Lightsail.+USD 12")
        self.assertRegex(portfolio, r"(?i)S3.+usage")
        self.assertRegex(portfolio, r"(?i)KMS.+USD 1")

    def test_original_monitoring_and_content_gates_remain_explicitly_outstanding(self) -> None:
        """Break caught: a security deviation or human editorial gate is reported as complete."""

        portfolio = read("docs/portfolio.md")
        operations = read("docs/specs/004-operations.md")
        product = read("docs/specs/000-product.md")
        for phrase in ("owner approval", "Grafana", "cAdvisor"):
            self.assertIn(phrase, operations)
            self.assertIn(phrase, portfolio)
        self.assertRegex(operations, r"(?i)acceptance remains pending")
        self.assertRegex(product, r"(?i)twelve.+owner-reviewed.+published")
        self.assertRegex(product, r"(?i)acceptance remains pending")

    def test_content_pack_keeps_all_twelve_sourced_candidates_unapproved(self) -> None:
        """Break caught: a draft loses its source/review gate or looks production-ready."""

        content = read("docs/content-review.md")
        self.assertIn("not seed data", content.lower())
        self.assertIn("not published", content.lower())
        self.assertIn("not owner-approved", content.lower())
        self.assertNotRegex(content, r"(?i)- \[[xX]\]")

        headings = list(re.finditer(r"(?m)^### (\d+)\. .+$", content))
        self.assertEqual([int(match.group(1)) for match in headings], list(range(1, 13)))
        blocks: list[str] = []
        for index, heading in enumerate(headings):
            end = headings[index + 1].start() if index + 1 < len(headings) else len(content)
            blocks.append(content[heading.start() : end])

        source_urls: list[str] = []
        for position, block in enumerate(blocks, start=1):
            self.assertRegex(block, r"Korean meaning: .*[가-힣]")
            self.assertIn("Original en-GB example:", block)
            self.assertIn("US comparison:", block)
            self.assertIn("Tags:", block)
            source_match = re.search(
                r"Source: \[Cambridge Dictionary\]\((https://dictionary\.cambridge\.org/(?:us/)?dictionary/[^)]+)\)",
                block,
            )
            self.assertIsNotNone(source_match, f"candidate {position} has no exact source page")
            source_urls.append(source_match.group(1))
            self.assertEqual(block.count("- [ ]"), 4, f"candidate {position}")

        self.assertEqual(len(set(source_urls)), 12)


if __name__ == "__main__":
    unittest.main()
