from __future__ import annotations

import importlib.util
import json
import re
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml


INFRA_ROOT = Path(__file__).resolve().parents[1]
TOFU_ROOT = INFRA_ROOT / "tofu"
ANSIBLE_ROOT = INFRA_ROOT / "ansible"


def read_text(path: Path) -> str:
    if not path.is_file():
        raise AssertionError(f"required infrastructure asset is missing: {path.relative_to(INFRA_ROOT)}")
    return path.read_text(encoding="utf-8")


def load_python_module(path: Path, name: str) -> ModuleType:
    if not path.is_file():
        raise AssertionError(f"required infrastructure asset is missing: {path.relative_to(INFRA_ROOT)}")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load infrastructure module: {path.relative_to(INFRA_ROOT)}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def playbook_tasks(path: Path) -> list[dict[str, Any]]:
    plays = yaml.safe_load(read_text(path))
    tasks: list[dict[str, Any]] = []

    def append_task(task: dict[str, Any]) -> None:
        tasks.append(task)
        for section in ("block", "rescue", "always"):
            for nested_task in task.get(section, []):
                append_task(nested_task)

    for play in plays:
        for section in ("pre_tasks", "tasks", "post_tasks"):
            for task in play.get(section, []):
                append_task(task)
    return tasks


def find_task(tasks: list[dict[str, Any]], module_name: str) -> dict[str, Any]:
    matches = [task for task in tasks if module_name in task]
    if len(matches) != 1:
        raise AssertionError(f"expected exactly one {module_name} task, found {len(matches)}")
    return matches[0]


@dataclass(frozen=True)
class HclBlock:
    kind: str
    labels: tuple[str, ...]
    body: str


HCL_BLOCK_HEADER = re.compile(
    r'(?m)^[ \t]*(?P<kind>[A-Za-z_][A-Za-z0-9_-]*)'
    r'(?P<labels>(?:[ \t]+"(?:[^"\\]|\\.)*")*)[ \t]*\{'
)


def matching_brace(source: str, opening_index: int) -> int:
    depth = 0
    in_string = False
    escaped = False
    for index in range(opening_index, len(source)):
        character = source[index]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return index
    raise ValueError("unterminated HCL block")


def hcl_blocks(source: str, kind: str, labels: tuple[str, ...] | None = None) -> list[HclBlock]:
    blocks: list[HclBlock] = []
    for match in HCL_BLOCK_HEADER.finditer(source):
        if match.group("kind") != kind:
            continue
        parsed_labels = tuple(
            json.loads(label)
            for label in re.findall(r'"(?:[^"\\]|\\.)*"', match.group("labels"))
        )
        if labels is not None and parsed_labels != labels:
            continue
        closing_index = matching_brace(source, match.end() - 1)
        blocks.append(HclBlock(kind, parsed_labels, source[match.end() : closing_index]))
    return blocks


def hcl_assignment(block: str, name: str) -> str | None:
    match = re.search(rf'(?m)^[ \t]*{re.escape(name)}[ \t]*=[ \t]*', block)
    if match is None:
        return None
    start = match.end()
    stack: list[str] = []
    pairs = {"[": "]", "(": ")", "{": "}"}
    in_string = False
    escaped = False
    for index in range(start, len(block)):
        character = block[index]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in pairs:
            stack.append(pairs[character])
        elif stack and character == stack[-1]:
            stack.pop()
        elif character == "\n" and not stack:
            return block[start:index].strip()
    return block[start:].strip()


def hcl_list(expression: str | None) -> list[str] | None:
    if expression is None or not expression.startswith("[") or not expression.endswith("]"):
        return None
    values: list[str] = []
    start = 1
    in_string = False
    escaped = False
    depth = 0
    for index in range(1, len(expression) - 1):
        character = expression[index]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[({":
            depth += 1
        elif character in "]) }".replace(" ", ""):
            depth -= 1
        elif character == "," and depth == 0:
            values.append(expression[start:index].strip())
            start = index + 1
    tail = expression[start:-1].strip()
    if tail:
        values.append(tail)
    return values


def backup_security_contract_errors(source: str) -> list[str]:
    errors: list[str] = []

    def require_one(kind: str, labels: tuple[str, ...], label: str) -> HclBlock | None:
        matches = hcl_blocks(source, kind, labels)
        if len(matches) != 1:
            errors.append(f"{label} must occur exactly once")
            return None
        return matches[0]

    kms = require_one("resource", ("aws_kms_key", "backups"), "backup KMS key")
    if kms is not None:
        expected_kms = {
            "key_usage": '"ENCRYPT_DECRYPT"',
            "customer_master_key_spec": '"SYMMETRIC_DEFAULT"',
            "enable_key_rotation": "true",
            "deletion_window_in_days": "30",
            "policy": "data.aws_iam_policy_document.backup_kms.json",
        }
        for name, expected in expected_kms.items():
            if hcl_assignment(kms.body, name) != expected:
                errors.append(f"backup KMS key {name} must equal {expected}")

    alias = require_one("resource", ("aws_kms_alias", "backups"), "backup KMS alias")
    if alias is not None and hcl_assignment(alias.body, "target_key_id") != "aws_kms_key.backups.key_id":
        errors.append("backup KMS alias must target the backup key")

    bucket = require_one("resource", ("aws_s3_bucket", "backups"), "backup bucket")
    if bucket is not None and hcl_assignment(bucket.body, "force_destroy") != "false":
        errors.append("backup bucket force_destroy must remain false")

    encryption_resources = [
        block
        for block in hcl_blocks(source, "resource")
        if block.labels and block.labels[0] == "aws_s3_bucket_server_side_encryption_configuration"
    ]
    if len(encryption_resources) != 1 or encryption_resources[0].labels != (
        "aws_s3_bucket_server_side_encryption_configuration",
        "backups",
    ):
        errors.append("exactly one backup bucket encryption configuration is allowed")
    else:
        encryption = encryption_resources[0]
        if hcl_assignment(encryption.body, "bucket") != "aws_s3_bucket.backups.id":
            errors.append("encryption configuration must target the backup bucket")
        rules = hcl_blocks(encryption.body, "rule", ())
        if len(rules) != 1:
            errors.append("backup encryption must have exactly one rule")
        else:
            if hcl_assignment(rules[0].body, "bucket_key_enabled") != "true":
                errors.append("S3 Bucket Key must be enabled")
            defaults = hcl_blocks(rules[0].body, "apply_server_side_encryption_by_default", ())
            if len(defaults) != 1:
                errors.append("backup encryption must have one default encryption block")
            else:
                if hcl_assignment(defaults[0].body, "sse_algorithm") != '"aws:kms"':
                    errors.append("backup encryption algorithm must be aws:kms")
                if hcl_assignment(defaults[0].body, "kms_master_key_id") != "aws_kms_key.backups.arn":
                    errors.append("backup encryption must use the backup KMS key ARN")

    public = require_one("resource", ("aws_s3_bucket_public_access_block", "backups"), "public-access block")
    if public is not None:
        for name in ("block_public_acls", "block_public_policy", "ignore_public_acls", "restrict_public_buckets"):
            if hcl_assignment(public.body, name) != "true":
                errors.append(f"{name} must remain true")

    versioning = require_one("resource", ("aws_s3_bucket_versioning", "backups"), "backup versioning")
    if versioning is not None:
        configurations = hcl_blocks(versioning.body, "versioning_configuration", ())
        if len(configurations) != 1 or hcl_assignment(configurations[0].body, "status") != '"Enabled"':
            errors.append("backup versioning must remain enabled")

    lifecycle = require_one("resource", ("aws_s3_bucket_lifecycle_configuration", "backups"), "backup lifecycle")
    if lifecycle is not None:
        expected_retention = {
            "days": "var.backup_retention_days",
            "noncurrent_days": "var.backup_retention_days",
            "days_after_initiation": "1",
        }
        for name, expected in expected_retention.items():
            values = [hcl_assignment(block.body, name) for block in hcl_blocks(lifecycle.body, "rule", ())]
            if expected not in values and hcl_assignment(lifecycle.body, name) != expected:
                nested_values = [
                    hcl_assignment(block.body, name)
                    for kind in ("expiration", "noncurrent_version_expiration", "abort_incomplete_multipart_upload")
                    for block in hcl_blocks(lifecycle.body, kind, ())
                ]
                if expected not in nested_values:
                    errors.append(f"backup lifecycle {name} must equal {expected}")

    policies = hcl_blocks(source, "data", ("aws_iam_policy_document", "backup_kms"))
    if len(policies) != 1:
        errors.append("backup KMS policy document must occur exactly once")
        return errors

    statements: dict[str, HclBlock] = {}
    for statement in hcl_blocks(policies[0].body, "statement", ()):
        sid_expression = hcl_assignment(statement.body, "sid")
        if sid_expression is None or not sid_expression.startswith('"'):
            errors.append("each KMS policy statement must have a literal SID")
            continue
        sid = json.loads(sid_expression)
        if sid in statements:
            errors.append(f"duplicate KMS policy SID: {sid}")
        statements[sid] = statement

        actions = hcl_list(hcl_assignment(statement.body, "actions")) or []
        if '"kms:*"' in actions:
            errors.append(f"{sid} must not grant kms:*")
        if '"kms:CreateGrant"' in actions:
            errors.append(f"{sid} must not grant kms:CreateGrant")
        principals = hcl_blocks(statement.body, "principals", ())
        expected_principal = '["arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:root"]'
        if len(principals) != 1 or hcl_assignment(principals[0].body, "identifiers") != expected_principal:
            errors.append(f"{sid} must use only the owning account principal")

    if set(statements) != {"AllowAccountKeyAdministration", "AllowAccountUseThroughS3"}:
        errors.append("KMS policy must contain only the administration and S3-use statements")
        return errors

    use_statement = statements["AllowAccountUseThroughS3"]
    conditions: dict[str, list[str] | None] = {}
    for condition in hcl_blocks(use_statement.body, "condition", ()):
        variable = hcl_assignment(condition.body, "variable")
        test = hcl_assignment(condition.body, "test")
        if variable is None or not variable.startswith('"') or test != '"StringEquals"':
            errors.append("S3-use conditions must use literal StringEquals variables")
            continue
        conditions[json.loads(variable)] = hcl_list(hcl_assignment(condition.body, "values"))
    expected_conditions = {
        "kms:CallerAccount": ["data.aws_caller_identity.current.account_id"],
        "kms:ViaService": ['"s3.${var.aws_region}.${data.aws_partition.current.dns_suffix}"'],
        "kms:EncryptionContext:aws:s3:arn": ["aws_s3_bucket.backups.arn"],
    }
    if conditions != expected_conditions:
        errors.append("S3-use policy must bind the account, regional S3 service and exact backup bucket context")

    return errors


class OpenTofuContractTests(unittest.TestCase):
    def test_required_assets_exist(self) -> None:
        required = [
            TOFU_ROOT / "versions.tf",
            TOFU_ROOT / "provider.tf",
            TOFU_ROOT / "variables.tf",
            TOFU_ROOT / "compute.tf",
            TOFU_ROOT / "dns.tf",
            TOFU_ROOT / "backup.tf",
            TOFU_ROOT / "budget.tf",
            TOFU_ROOT / "outputs.tf",
            TOFU_ROOT / "terraform.tfvars.example",
            TOFU_ROOT / "backend.tf.example",
            TOFU_ROOT / "remote-state.md",
            TOFU_ROOT / ".terraform.lock.hcl",
        ]
        missing = [str(path.relative_to(INFRA_ROOT)) for path in required if not path.is_file()]
        self.assertEqual([], missing, f"missing OpenTofu assets: {missing}")

    def test_versions_and_provider_are_pinned(self) -> None:
        versions = read_text(TOFU_ROOT / "versions.tf")
        lock = read_text(TOFU_ROOT / ".terraform.lock.hcl")

        self.assertRegex(versions, r'required_version\s*=\s*">= 1\.12\.0, < 1\.13\.0"')
        provider_version = re.search(r'aws\s*=\s*\{.*?version\s*=\s*"= ([0-9.]+)"', versions, re.S)
        self.assertIsNotNone(provider_version, "AWS provider must use an exact version constraint")
        assert provider_version is not None
        self.assertIn(f'version     = "{provider_version.group(1)}"', lock)
        self.assertGreaterEqual(lock.count('"h1:'), 2, "lock must cover linux_amd64 and windows_amd64")
        self.assertGreaterEqual(lock.count('"zh:'), 1, "lock must retain registry package checksums")

    def test_provider_lock_policy_requires_both_deployment_platforms(self) -> None:
        policy_path = TOFU_ROOT / "provider-lock-policy.json"
        policy = json.loads(read_text(policy_path))
        self.assertEqual("registry.opentofu.org/hashicorp/aws", policy["provider"])
        self.assertEqual("6.55.0", policy["version"])
        self.assertEqual(["linux_amd64", "windows_amd64"], policy["platforms"])

        verifier = load_python_module(
            TOFU_ROOT / "verify_provider_lock.py",
            "verify_provider_lock",
        )
        self.assertEqual(
            [
                "tofu",
                "providers",
                "lock",
                "-platform=linux_amd64",
                "-platform=windows_amd64",
            ],
            verifier.build_lock_command("tofu", policy),
        )

    def test_provider_lock_runner_decodes_utf8_output_independent_of_locale(self) -> None:
        verifier = load_python_module(
            TOFU_ROOT / "verify_provider_lock.py",
            "verify_provider_lock_utf8",
        )
        completed = verifier.run_text_command(
            [
                sys.executable,
                "-c",
                "import os; os.write(2, 'provider lock ✓'.encode('utf-8'))",
            ],
            TOFU_ROOT,
        )

        self.assertEqual(0, completed.returncode)
        self.assertEqual("provider lock ✓", completed.stderr)

    def test_seoul_lightsail_host_and_static_ip_are_declared(self) -> None:
        provider = read_text(TOFU_ROOT / "provider.tf")
        variables = read_text(TOFU_ROOT / "variables.tf")
        compute = read_text(TOFU_ROOT / "compute.tf")

        self.assertRegex(variables, r'(?s)variable\s+"aws_region".*?default\s*=\s*"ap-northeast-2"')
        self.assertRegex(variables, r'(?s)variable\s+"availability_zone".*?default\s*=\s*"ap-northeast-2a"')
        self.assertIn("region = var.aws_region", provider)
        self.assertIn('resource "aws_lightsail_instance"', compute)
        self.assertIn("availability_zone = var.availability_zone", compute)
        self.assertIn("blueprint_id      = var.lightsail_blueprint_id", compute)
        self.assertIn("bundle_id         = var.lightsail_bundle_id", compute)
        self.assertRegex(variables, r'(?s)variable\s+"lightsail_blueprint_id".*?default\s*=\s*"ubuntu_24_04"')
        self.assertRegex(variables, r'(?s)variable\s+"lightsail_bundle_id".*?default\s*=\s*"small_3_0"')
        self.assertIn('resource "aws_lightsail_static_ip"', compute)
        self.assertIn('resource "aws_lightsail_static_ip_attachment"', compute)

    def test_firewall_exposes_only_web_and_restricted_ssh(self) -> None:
        variables = read_text(TOFU_ROOT / "variables.tf")
        compute = read_text(TOFU_ROOT / "compute.tf")

        self.assertIn('resource "aws_lightsail_instance_public_ports"', compute)
        for port in (80, 443):
            self.assertRegex(
                compute,
                rf'(?s)from_port\s*=\s*{port}.*?to_port\s*=\s*{port}.*?cidrs\s*=\s*\["0\.0\.0\.0/0"\]',
            )
        self.assertRegex(
            compute,
            r'(?s)from_port\s*=\s*22.*?to_port\s*=\s*22.*?cidrs\s*=\s*var\.operator_ssh_access\.cidrs',
        )
        self.assertNotRegex(compute, r'from_port\s*=\s*(3000|5432|8080|9090)')
        self.assertRegex(
            variables,
            r'(?s)variable\s+"operator_ssh_access".*?allow_world_ssh.*?0\.0\.0\.0/0.*?error_message',
        )

    def test_optional_dns_targets_the_static_ip(self) -> None:
        dns = read_text(TOFU_ROOT / "dns.tf")
        self.assertIn('resource "aws_lightsail_domain"', dns)
        self.assertRegex(dns, r'count\s*=\s*var\.domain_name\s*==\s*null\s*\?\s*0\s*:\s*1')
        self.assertIn("target      = aws_lightsail_static_ip.this.ip_address", dns)

    def test_backup_bucket_is_private_encrypted_versioned_and_retained(self) -> None:
        backup = read_text(TOFU_ROOT / "backup.tf")
        self.assertIn('resource "aws_s3_bucket_server_side_encryption_configuration"', backup)
        self.assertRegex(backup, r'status\s*=\s*"Enabled"')
        for setting in (
            "block_public_acls",
            "block_public_policy",
            "ignore_public_acls",
            "restrict_public_buckets",
        ):
            self.assertRegex(backup, rf'{setting}\s*=\s*true')
        self.assertIn("days = var.backup_retention_days", backup)
        self.assertIn("force_destroy = false", backup)

    def test_backup_bucket_uses_customer_managed_kms_with_s3_bucket_key(self) -> None:
        backup = read_text(TOFU_ROOT / "backup.tf")
        self.assertEqual([], backup_security_contract_errors(backup))

    def test_backup_kms_semantic_contract_rejects_security_mutations(self) -> None:
        backup = read_text(TOFU_ROOT / "backup.tf")
        self.assertEqual([], backup_security_contract_errors(backup), "the unmodified fixture must be valid")

        without_context = re.sub(
            r'\n\s*condition\s*\{\s*test\s*=\s*"StringEquals"\s*'
            r'variable\s*=\s*"kms:EncryptionContext:aws:s3:arn".*?\n\s*\}',
            "",
            backup,
            count=1,
            flags=re.S,
        )
        competing_encryption = backup + """

resource "aws_s3_bucket_server_side_encryption_configuration" "competing" {
  bucket = aws_s3_bucket.backups.id
}
"""
        mutations = {
            "missing bucket encryption context": without_context,
            "wrong bucket encryption context": backup.replace(
                "values   = [aws_s3_bucket.backups.arn]",
                "values   = [aws_s3_bucket.other.arn]",
                1,
            ),
            "wildcard KMS action": backup.replace('"kms:Encrypt",', '"kms:*",', 1),
            "wildcard principal": backup.replace(
                'identifiers = ["arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:root"]',
                'identifiers = ["*"]',
                1,
            ),
            "wrong encryption key identity": backup.replace(
                "kms_master_key_id = aws_kms_key.backups.arn",
                "kms_master_key_id = aws_kms_key.backups.key_id",
                1,
            ),
            "wrong encryption algorithm": backup.replace('sse_algorithm     = "aws:kms"', 'sse_algorithm     = "AES256"', 1),
            "disabled rotation": backup.replace("enable_key_rotation      = true", "enable_key_rotation      = false", 1),
            "displaced bucket encryption": backup.replace(
                'resource "aws_s3_bucket_server_side_encryption_configuration" "backups" {\n  bucket = aws_s3_bucket.backups.id',
                'resource "aws_s3_bucket_server_side_encryption_configuration" "backups" {\n  bucket = aws_s3_bucket.other.id',
                1,
            ),
            "competing bucket encryption": competing_encryption,
            "public access weakened": backup.replace("block_public_policy     = true", "block_public_policy     = false", 1),
            "versioning disabled": backup.replace('status = "Enabled"', 'status = "Suspended"', 1),
            "retention displaced": backup.replace(
                "days = var.backup_retention_days",
                "days = 1",
                1,
            ),
        }
        for name, mutated in mutations.items():
            with self.subTest(name=name):
                self.assertNotEqual(backup, mutated, "mutation did not alter the fixture")
                self.assertTrue(backup_security_contract_errors(mutated), "mutation must violate the semantic contract")

    def test_budget_has_absolute_usd_12_and_15_notifications(self) -> None:
        variables = read_text(TOFU_ROOT / "variables.tf")
        budget = read_text(TOFU_ROOT / "budget.tf")
        self.assertIn('resource "aws_budgets_budget"', budget)
        self.assertIn('limit_unit   = "USD"', budget)
        self.assertEqual(2, budget.count('threshold_type             = "ABSOLUTE_VALUE"'))
        self.assertRegex(budget, r'threshold\s*=\s*12')
        self.assertRegex(budget, r'threshold\s*=\s*15')
        self.assertIn("subscriber_email_addresses = [var.budget_notification_email]", budget)
        self.assertRegex(variables, r'(?s)variable\s+"budget_notification_email".*?validation\s*\{.*?can\(regex\(')

    def test_remote_state_example_contains_no_account_or_credentials(self) -> None:
        backend = read_text(TOFU_ROOT / "backend.tf.example")
        guide = read_text(TOFU_ROOT / "remote-state.md")
        combined = f"{backend}\n{guide}"
        self.assertIn('backend "s3" {}', backend)
        self.assertNotRegex(combined, r'AKIA[0-9A-Z]{16}')
        self.assertNotRegex(combined, r'(?i)(access_key|secret_key)\s*=')
        self.assertNotRegex(backend, r'(?m)^\s*(bucket|key)\s*=')
        self.assertIn("use_lockfile", guide)

    def test_outputs_and_repository_assets_do_not_expose_secrets_or_state(self) -> None:
        outputs = read_text(TOFU_ROOT / "outputs.tf")
        self.assertNotRegex(outputs, r'(?i)output\s+".*(secret|password|token|credential)')

        forbidden_names = {"terraform.tfstate", "terraform.tfstate.backup", "secrets.yml", "inventory.ini"}
        offending_names = [str(path.relative_to(INFRA_ROOT)) for path in INFRA_ROOT.rglob("*") if path.name in forbidden_names]
        self.assertEqual([], offending_names)

        material = "\n".join(
            path.read_text(encoding="utf-8", errors="ignore")
            for root in (TOFU_ROOT, ANSIBLE_ROOT)
            if root.exists()
            for path in root.rglob("*")
            if path.is_file()
            and ".terraform" not in path.parts
            and path.suffix in {".cfg", ".example", ".hcl", ".ini", ".j2", ".md", ".tf", ".txt", ".yml"}
        )
        self.assertNotRegex(material, r'AKIA[0-9A-Z]{16}')
        self.assertNotIn("BEGIN OPENSSH PRIVATE KEY", material)


class AnsibleContractTests(unittest.TestCase):
    def test_required_assets_exist(self) -> None:
        required = [
            ANSIBLE_ROOT / "ansible.cfg",
            ANSIBLE_ROOT / "requirements.txt",
            ANSIBLE_ROOT / "requirements.yml",
            ANSIBLE_ROOT / "inventories" / "production.example.ini",
            ANSIBLE_ROOT / "group_vars" / "all.example.yml",
            ANSIBLE_ROOT / "group_vars" / "secrets.example.yml",
            ANSIBLE_ROOT / "playbooks" / "provision.yml",
            ANSIBLE_ROOT / "playbooks" / "deploy.yml",
            ANSIBLE_ROOT / "playbooks" / "templates" / "sshd-hardening.conf.j2",
            ANSIBLE_ROOT / "playbooks" / "templates" / "docker-daemon.json.j2",
            ANSIBLE_ROOT / "README.md",
        ]
        missing = [str(path.relative_to(INFRA_ROOT)) for path in required if not path.is_file()]
        self.assertEqual([], missing, f"missing Ansible assets: {missing}")

    def test_dependencies_and_collections_are_exactly_pinned(self) -> None:
        python_requirements = read_text(ANSIBLE_ROOT / "requirements.txt").splitlines()
        self.assertTrue(any(re.fullmatch(r"ansible-core==\d+\.\d+\.\d+", line) for line in python_requirements))
        self.assertTrue(any(re.fullmatch(r"ansible-lint==\d+\.\d+\.\d+", line) for line in python_requirements))

        galaxy = yaml.safe_load(read_text(ANSIBLE_ROOT / "requirements.yml"))
        collections = {item["name"]: item.get("version") for item in galaxy["collections"]}
        self.assertRegex(collections.get("community.docker", ""), r"^\d+\.\d+\.\d+$")
        self.assertTrue(all(re.fullmatch(r"\d+\.\d+\.\d+", version or "") for version in collections.values()))

    def test_ansible_core_pin_is_stable_and_patched_for_cve_2026_11332(self) -> None:
        requirements = read_text(ANSIBLE_ROOT / "requirements.txt").splitlines()
        core_pin = next(line for line in requirements if line.startswith("ansible-core=="))
        match = re.fullmatch(r"ansible-core==(\d+)\.(\d+)\.(\d+)", core_pin)

        self.assertIsNotNone(match, "ansible-core must use an exact stable release")
        self.assertEqual((2, 18), tuple(map(int, match.groups()[:2])))
        self.assertGreaterEqual(
            int(match.group(3)),
            18,
            "ansible-core 2.18 must include the CVE-2026-11332 fix",
        )

    def test_inventory_and_examples_contain_names_not_real_secrets(self) -> None:
        inventory = read_text(ANSIBLE_ROOT / "inventories" / "production.example.ini")
        secrets = yaml.safe_load(read_text(ANSIBLE_ROOT / "group_vars" / "secrets.example.yml"))
        self.assertIn("example.invalid", inventory)
        self.assertNotRegex(inventory, r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
        self.assertNotRegex(inventory, r"(?i)private_key_file\s*=")
        self.assertEqual(
            {
                "operator_ssh_public_key",
                "alright_mate_delivery_ssh_public_key",
                "ghcr_username",
                "ghcr_token",
            },
            set(secrets),
        )
        self.assertTrue(all(value is None for value in secrets.values()))

    def test_provision_playbook_declares_host_security_and_docker_contract(self) -> None:
        playbook = read_text(ANSIBLE_ROOT / "playbooks" / "provision.yml")
        self.assertIn("https://download.docker.com/linux/ubuntu", playbook)
        self.assertIn("docker-compose-plugin", playbook)
        self.assertIn("alright_mate_deploy_user", playbook)
        self.assertIn("/opt/alright-mate", playbook)
        self.assertIn("community.general.ufw", playbook)
        for port in (22, 80, 443):
            self.assertRegex(playbook, rf"port:\s*\"?{port}\"?")
        self.assertIn("unattended-upgrades", playbook)
        self.assertIn("sshd-hardening.conf.j2", playbook)
        self.assertIn("docker-daemon.json.j2", playbook)

    def test_provision_prepares_the_locked_backup_service_identity(self) -> None:
        tasks = playbook_tasks(ANSIBLE_ROOT / "playbooks" / "provision.yml")
        task_names = [task.get("name") for task in tasks]
        group_name = "Create the backup service group"
        user_name = "Create the locked backup service account"

        self.assertIn(group_name, task_names)
        self.assertIn(user_name, task_names)

        group_task = tasks[task_names.index(group_name)]["ansible.builtin.group"]
        self.assertEqual("alright-mate-backup", group_task.get("name"))
        self.assertEqual("present", group_task.get("state"))
        self.assertTrue(group_task.get("system"))

        user_task = tasks[task_names.index(user_name)]["ansible.builtin.user"]
        self.assertEqual("alright-mate-backup", user_task.get("name"))
        self.assertEqual("alright-mate-backup", user_task.get("group"))
        self.assertEqual(["{{ alright_mate_deploy_group }}"], user_task.get("groups"))
        self.assertIs(user_task.get("append"), False)
        self.assertNotIn("docker", user_task.get("groups", []))
        drifted_groups = {"docker", "unexpected-operator-group"}
        desired_groups = set(user_task.get("groups", []))
        converged_groups = (
            drifted_groups | desired_groups
            if user_task.get("append", False)
            else desired_groups
        )
        self.assertEqual({"{{ alright_mate_deploy_group }}"}, converged_groups)
        self.assertNotIn("docker", converged_groups)
        self.assertFalse(user_task.get("create_home"))
        self.assertTrue(user_task.get("password_lock"))
        self.assertEqual("/usr/sbin/nologin", user_task.get("shell"))
        self.assertEqual("present", user_task.get("state"))
        self.assertTrue(user_task.get("system"))

        directory_tasks = [
            task
            for task in tasks
            if task.get("ansible.builtin.file", {}).get("state") == "directory"
        ]
        managed_directories = [
            item
            for task in directory_tasks
            for item in task.get("loop", [])
            if isinstance(item, dict)
        ]
        self.assertIn(
            {
                "path": "/etc/alright-mate",
                "owner": "root",
                "group": "alright-mate-backup",
                "mode": "0750",
            },
            managed_directories,
        )

    def test_provision_installs_only_the_versioned_constrained_delivery_helper(self) -> None:
        tasks = playbook_tasks(ANSIBLE_ROOT / "playbooks" / "provision.yml")
        task_names = [task.get("name") for task in tasks]
        helper_name = "Install the versioned delivery helper"
        ssh_boundary_name = "Install the forced-command delivery SSH boundary"
        sudoers_name = "Authorise only constrained delivery helper actions"

        self.assertIn(helper_name, task_names)
        self.assertIn(ssh_boundary_name, task_names)
        self.assertIn(sudoers_name, task_names)
        helper = tasks[task_names.index(helper_name)]["ansible.builtin.copy"]
        self.assertEqual(
            "{{ playbook_dir }}/../../../scripts/delivery/remote-release.sh",
            helper.get("src"),
        )
        self.assertEqual("/usr/local/libexec/alright-mate-delivery", helper.get("dest"))
        self.assertEqual("root", helper.get("owner"))
        self.assertEqual("root", helper.get("group"))
        self.assertEqual("0755", helper.get("mode"))

        ssh_boundary = tasks[task_names.index(ssh_boundary_name)]["ansible.builtin.copy"]
        self.assertEqual(
            "{{ playbook_dir }}/../../../scripts/delivery/ssh-command.sh",
            ssh_boundary.get("src"),
        )
        self.assertEqual(
            "/usr/local/libexec/alright-mate-delivery-ssh",
            ssh_boundary.get("dest"),
        )
        self.assertEqual("root", ssh_boundary.get("owner"))
        self.assertEqual("root", ssh_boundary.get("group"))
        self.assertEqual("0755", ssh_boundary.get("mode"))

        sudoers = tasks[task_names.index(sudoers_name)]["ansible.builtin.copy"]
        self.assertEqual("/etc/sudoers.d/alright-mate-delivery", sudoers.get("dest"))
        self.assertEqual("root", sudoers.get("owner"))
        self.assertEqual("root", sudoers.get("group"))
        self.assertEqual("0440", sudoers.get("mode"))
        self.assertEqual("/usr/sbin/visudo -cf %s", sudoers.get("validate"))
        content = sudoers.get("content", "")
        for action in ("begin-deploy", "begin-rollback", "status", "finish", "abort", "verify-runtime"):
            self.assertIn(f"/usr/local/libexec/alright-mate-delivery {action} ", content)
        self.assertNotIn("/bin/bash", content)
        self.assertNotIn("/bin/sh", content)

    def test_provision_separates_operator_from_the_unprivileged_delivery_identity(self) -> None:
        playbook_path = ANSIBLE_ROOT / "playbooks" / "provision.yml"
        playbook = yaml.safe_load(read_text(playbook_path))[0]
        tasks = playbook_tasks(playbook_path)
        task_names = [task.get("name") for task in tasks]

        deployment_user = tasks[
            task_names.index("Create the dedicated unprivileged deployment user")
        ]["ansible.builtin.user"]
        self.assertEqual([], deployment_user.get("groups"))
        self.assertIs(deployment_user.get("append"), False)
        self.assertNotIn("docker", deployment_user.get("groups", []))
        self.assertTrue(deployment_user.get("password_lock"))

        assertions = [
            condition
            for task in playbook.get("pre_tasks", [])
            for condition in task.get("ansible.builtin.assert", {}).get("that", [])
        ]
        self.assertIn("operator_user != alright_mate_deploy_user", assertions)
        self.assertIn("alright_mate_delivery_ssh_public_key | length > 0", assertions)

        key_task_name = "Authorise the restricted delivery public key"
        self.assertIn(key_task_name, task_names)
        delivery_key = tasks[task_names.index(key_task_name)]["ansible.posix.authorized_key"]
        self.assertEqual("{{ alright_mate_deploy_user }}", delivery_key.get("user"))
        self.assertEqual("{{ alright_mate_delivery_ssh_public_key }}", delivery_key.get("key"))
        self.assertEqual(
            'command="/usr/local/libexec/alright-mate-delivery-ssh",restrict',
            delivery_key.get("key_options"),
        )
        self.assertIs(delivery_key.get("exclusive"), True)

    def test_deploy_uses_root_only_for_docker_without_persistent_group_access(self) -> None:
        provision_tasks = playbook_tasks(ANSIBLE_ROOT / "playbooks" / "provision.yml")
        provision_by_name = {task.get("name"): task for task in provision_tasks}
        deployment_user = provision_by_name[
            "Create the dedicated unprivileged deployment user"
        ]["ansible.builtin.user"]
        self.assertEqual([], deployment_user.get("groups"))
        self.assertIs(deployment_user.get("append"), False)

        deploy_tasks = playbook_tasks(ANSIBLE_ROOT / "playbooks" / "deploy.yml")
        deploy_by_name = {task.get("name"): task for task in deploy_tasks}
        for task_name in (
            "Render and validate the resolved production Compose project",
            "Enumerate every resolved production image",
            "Pull and start the immutable release",
        ):
            with self.subTest(task=task_name):
                task = deploy_by_name[task_name]
                self.assertIs(task.get("become"), True)
                self.assertEqual("root", task.get("become_user"))

        docker_block = deploy_by_name["Pull and start the immutable release"]
        docker_modules = {
            module
            for task in docker_block["block"] + docker_block["always"]
            for module in task
            if module.startswith("community.docker.")
        }
        self.assertEqual(
            {
                "community.docker.docker_compose_v2",
                "community.docker.docker_image_info",
                "community.docker.docker_image_pull",
                "community.docker.docker_login",
            },
            docker_modules,
        )
        for task_name in (
            "Create the immutable release directory",
            "Copy the versioned runtime configuration",
            "Copy the injected runtime environment",
            "Point current at the successfully reconciled release",
        ):
            with self.subTest(ownership_task=task_name):
                task = deploy_by_name[task_name]
                module = next(value for key, value in task.items() if key.startswith("ansible.builtin."))
                self.assertEqual("{{ alright_mate_deploy_user }}", module.get("owner"))

    def test_operator_cidrs_are_canonical_and_world_variants_are_rejected(self) -> None:
        policy = load_python_module(
            ANSIBLE_ROOT / "filter_plugins" / "alright_mate_policy.py",
            "alright_mate_policy",
        )

        self.assertEqual(
            ["203.0.113.10/32", "2001:db8::/64"],
            policy.normalise_operator_ssh_cidrs(
                ["203.0.113.10/32", "2001:0DB8:0000:0000::/64", "203.0.113.10/32"]
            ),
        )
        for unsafe in (
            ["any"],
            ["ANY"],
            ["0.0.0.0/0"],
            ["0.0.0.0/00"],
            ["::/0"],
            ["0:0:0:0:0:0:0:0/0"],
            ["203.0.113.10"],
            ["203.0.113.10/24"],
            ["not-a-cidr"],
        ):
            with self.subTest(unsafe=unsafe):
                with self.assertRaises(ValueError):
                    policy.normalise_operator_ssh_cidrs(unsafe)
        for wrong_shape in (
            "203.0.113.10/32",
            {"203.0.113.10/32": True},
            [],
            [None],
        ):
            with self.subTest(wrong_shape=wrong_shape):
                with self.assertRaises(ValueError):
                    policy.normalise_operator_ssh_cidrs(wrong_shape)

    def test_ufw_policy_detects_drift_then_converges_without_a_second_reset(self) -> None:
        policy = load_python_module(
            ANSIBLE_ROOT / "filter_plugins" / "alright_mate_policy.py",
            "alright_mate_policy_ufw",
        )
        cidrs = ["203.0.113.10/32"]
        drifted = """Status: active
[ 1] 22/tcp ALLOW IN Anywhere
[ 2] 8080/tcp ALLOW IN Anywhere
"""
        converged = """Status: active
[ 1] 22/tcp ALLOW IN 203.0.113.10 # alright-mate:ssh:203.0.113.10/32
[ 2] 80/tcp ALLOW IN Anywhere # alright-mate:http
[ 3] 443/tcp ALLOW IN Anywhere # alright-mate:https
[ 4] 80/tcp (v6) ALLOW IN Anywhere (v6) # alright-mate:http
[ 5] 443/tcp (v6) ALLOW IN Anywhere (v6) # alright-mate:https
"""
        misleading_comment = converged.replace(
            "22/tcp ALLOW IN 203.0.113.10 # alright-mate:ssh:203.0.113.10/32",
            "22/tcp ALLOW IN 198.51.100.27 # alright-mate:ssh:203.0.113.10/32",
        )

        self.assertTrue(policy.ufw_policy_requires_reset(drifted, cidrs))
        self.assertTrue(policy.ufw_policy_requires_reset(misleading_comment, cidrs))
        self.assertFalse(policy.ufw_policy_requires_reset(converged, cidrs))
        self.assertFalse(policy.ufw_policy_requires_reset(converged, cidrs))

    def test_primary_gpg_fingerprint_does_not_accept_an_approved_subkey(self) -> None:
        policy = load_python_module(
            ANSIBLE_ROOT / "filter_plugins" / "alright_mate_policy.py",
            "alright_mate_policy_gpg",
        )
        approved_fingerprint = "9DC858229FC7DD38854AE2D88D81803C0EBFCD88"
        colon_output = f"""pub:-:4096:1:DEADBEEFDEADBEEF:0:0::::::scESC::::::23::0:
fpr:::::::::1111111111111111111111111111111111111111:
uid:-::::0::EXAMPLE::Docker Release (CE deb) <docker@example.invalid>::::::::::0:
sub:-:4096:1:CAFEBABECAFEBABE:0:0::::::e::::::23:
fpr:::::::::{approved_fingerprint}:
"""

        self.assertTrue(
            hasattr(policy, "primary_gpg_fingerprints"),
            "primary GPG fingerprint parser is missing",
        )
        self.assertEqual(
            ["1111111111111111111111111111111111111111"],
            policy.primary_gpg_fingerprints(colon_output),
        )

    def test_runtime_environment_cannot_override_release_or_image_identity(self) -> None:
        policy = load_python_module(
            ANSIBLE_ROOT / "filter_plugins" / "alright_mate_policy.py",
            "alright_mate_policy_environment",
        )
        self.assertTrue(
            hasattr(policy, "runtime_environment_reserved_keys"),
            "runtime environment policy filter is missing",
        )
        safe_environment = """SERVER_NAME=mate.example.invalid
POSTGRES_DB=alrightmate
# API_IMAGE=commented-out
"""
        unsafe_environment = """API_IMAGE=ghcr.io/example/api:latest
export MIGRATION_IMAGE=opaque
WEB_IMAGE = ghcr.io/example/web@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
BACKUP_TOOLS_IMAGE=ghcr.io/example/backup-tools@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
BUILD_VERSION=attacker-selected
RELEASE_SHA=0000000000000000000000000000000000000000
IMAGE_TAG=latest
"""

        self.assertEqual([], policy.runtime_environment_reserved_keys(safe_environment))
        self.assertEqual(
            [
                "API_IMAGE",
                "MIGRATION_IMAGE",
                "WEB_IMAGE",
                "BACKUP_TOOLS_IMAGE",
                "BUILD_VERSION",
                "RELEASE_SHA",
                "IMAGE_TAG",
            ],
            policy.runtime_environment_reserved_keys(unsafe_environment),
        )

    def test_provision_verifies_docker_key_before_repository_and_resets_drifted_ufw(self) -> None:
        tasks = playbook_tasks(ANSIBLE_ROOT / "playbooks" / "provision.yml")
        task_names = [task.get("name") for task in tasks]
        self.assertIn("Download the Docker repository signing key", task_names)
        self.assertIn("Verify the Docker repository signing key fingerprint", task_names)
        key_download_index = task_names.index("Download the Docker repository signing key")
        key_verify_index = task_names.index("Verify the Docker repository signing key fingerprint")
        repository_index = task_names.index("Configure the official Docker Engine repository")
        self.assertLess(key_download_index, key_verify_index)
        self.assertLess(key_verify_index, repository_index)

        download_task = tasks[key_download_index]
        self.assertEqual(
            "sha256:1500c1f56fa9e26b9b8f42452a553675796ade0807cdce11975eb98170b3a570",
            download_task["ansible.builtin.get_url"].get("checksum"),
        )

        verify_task = tasks[key_verify_index]
        self.assertEqual(False, verify_task.get("changed_when"))
        self.assertIn("docker_apt_key_fingerprint", str(verify_task.get("failed_when")))
        self.assertIn("primary_gpg_fingerprints", str(verify_task.get("failed_when")))

        reset_tasks = [
            task
            for task in tasks
            if task.get("community.general.ufw", {}).get("state") == "reset"
        ]
        self.assertEqual(1, len(reset_tasks))
        reset_task = reset_tasks[0]
        self.assertEqual("reset", reset_task["community.general.ufw"].get("state"))
        self.assertEqual("ufw_policy_reset_required", reset_task.get("when"))

    def test_deploy_playbook_requires_sha_and_uses_idempotent_compose(self) -> None:
        playbook = read_text(ANSIBLE_ROOT / "playbooks" / "deploy.yml")
        self.assertIn("release_sha is match('^[0-9a-f]{40}$')", playbook)
        self.assertIn("community.docker.docker_login", playbook)
        self.assertIn("no_log: true", playbook)
        self.assertIn("community.docker.docker_compose_v2", playbook)
        self.assertIn("state: present", playbook)
        self.assertIn("RELEASE_SHA", playbook)
        self.assertNotRegex(playbook, r"(?i)(image|tag).*latest")

    def test_deploy_renders_and_uses_base_plus_production_compose(self) -> None:
        tasks = playbook_tasks(ANSIBLE_ROOT / "playbooks" / "deploy.yml")
        compose_task = find_task(tasks, "community.docker.docker_compose_v2")
        compose_args = compose_task["community.docker.docker_compose_v2"]
        self.assertEqual(["compose.yaml", "compose.production.yaml"], compose_args.get("files"))

        render_tasks = [task for task in tasks if "ansible.builtin.command" in task]
        render_argv = [task["ansible.builtin.command"].get("argv", []) for task in render_tasks]
        self.assertIn(
            [
                "docker",
                "compose",
                "--project-directory",
                "{{ release_directory }}",
                "--env-file",
                "{{ release_directory }}/.env",
                "-f",
                "compose.yaml",
                "-f",
                "compose.production.yaml",
                "config",
                "--quiet",
            ],
            render_argv,
        )

    def test_deploy_supplies_digest_images_and_consumes_release_sha(self) -> None:
        tasks = playbook_tasks(ANSIBLE_ROOT / "playbooks" / "deploy.yml")
        compose_task = find_task(tasks, "community.docker.docker_compose_v2")
        environment = compose_task.get("environment", {})
        self.assertEqual("{{ api_image_reference }}", environment.get("API_IMAGE"))
        self.assertEqual("{{ migration_image_reference }}", environment.get("MIGRATION_IMAGE"))
        self.assertEqual("{{ web_image_reference }}", environment.get("WEB_IMAGE"))
        self.assertEqual("{{ release_sha }}", environment.get("BUILD_VERSION"))
        self.assertEqual("{{ release_sha }}", environment.get("RELEASE_SHA"))

        conditions = [
            condition
            for task in tasks
            for condition in task.get("ansible.builtin.assert", {}).get("that", [])
        ]
        for variable in (
            "api_image_reference",
            "migration_image_reference",
            "web_image_reference",
            "backup_image_reference",
        ):
            matching_conditions = [
                condition
                for condition in conditions
                if variable in condition and " is match(" in condition
            ]
            self.assertEqual(
                1,
                len(matching_conditions),
                f"{variable} must have one strict image-reference assertion",
            )
            self.assertIn("^ghcr\\.io/", matching_conditions[0])
            self.assertIn("@sha256:[0-9a-f]{64}$", matching_conditions[0])
        self.assertIn("runtime_environment_reserved_keys | length == 0", conditions)

        group_vars = yaml.safe_load(
            read_text(ANSIBLE_ROOT / "group_vars" / "all.example.yml")
        )
        self.assertIn("backup_image_reference", group_vars)
        self.assertIsNone(group_vars["backup_image_reference"])

    def test_deploy_keeps_backup_auxiliary_out_of_application_compose(self) -> None:
        tasks = playbook_tasks(ANSIBLE_ROOT / "playbooks" / "deploy.yml")
        compose_task = find_task(tasks, "community.docker.docker_compose_v2")
        environment = compose_task.get("environment", {})

        self.assertEqual(
            {"API_IMAGE", "BUILD_VERSION", "MIGRATION_IMAGE", "RELEASE_SHA", "WEB_IMAGE"},
            set(environment),
        )
        self.assertNotIn("BACKUP_TOOLS_IMAGE", environment)

    def test_deploy_rejects_one_mismatched_image_revision(self) -> None:
        policy = load_python_module(
            ANSIBLE_ROOT / "filter_plugins" / "alright_mate_policy.py",
            "alright_mate_policy_release_images",
        )
        release_sha = "0123456789abcdef0123456789abcdef01234567"
        references = [
            "ghcr.io/alright-mate/api@sha256:" + "a" * 64,
            "ghcr.io/alright-mate/migration@sha256:" + "b" * 64,
            "ghcr.io/alright-mate/web@sha256:" + "c" * 64,
            "ghcr.io/alright-mate/backup-tools@sha256:" + "d" * 64,
        ]
        inspection_results = {
            "results": [
                {
                    "item": reference,
                    "images": [
                        {
                            "Config": {
                                "Labels": {
                                    "org.opencontainers.image.revision": (
                                        "fedcba9876543210fedcba9876543210fedcba98"
                                        if index == 3
                                        else release_sha
                                    )
                                }
                            }
                        }
                    ],
                }
                for index, reference in enumerate(references)
            ]
        }

        self.assertTrue(
            hasattr(policy, "release_image_revision_mismatches"),
            "release-image revision binding filter is missing",
        )
        self.assertEqual(
            [references[3]],
            policy.release_image_revision_mismatches(inspection_results, release_sha),
        )

    def test_deploy_pulls_and_checks_all_four_revisions_before_compose_mutation(self) -> None:
        tasks = playbook_tasks(ANSIBLE_ROOT / "playbooks" / "deploy.yml")
        task_names = [task.get("name") for task in tasks]
        expected_references = [
            "{{ api_image_reference }}",
            "{{ migration_image_reference }}",
            "{{ web_image_reference }}",
            "{{ backup_image_reference }}",
        ]
        pull_index = task_names.index("Pull each immutable release image")
        inspect_index = task_names.index("Inspect each pulled release image")
        verify_index = task_names.index("Require every image revision to match the release SHA")
        compose_index = task_names.index("Reconcile the Docker Compose project")

        self.assertLess(pull_index, inspect_index)
        self.assertLess(inspect_index, verify_index)
        self.assertLess(verify_index, compose_index)
        self.assertEqual(expected_references, tasks[pull_index].get("loop"))
        self.assertEqual(expected_references, tasks[inspect_index].get("loop"))
        self.assertIn("community.docker.docker_image_pull", tasks[pull_index])
        self.assertEqual(
            "not_present",
            tasks[pull_index]["community.docker.docker_image_pull"].get("pull"),
        )
        self.assertIn("community.docker.docker_image_info", tasks[inspect_index])
        revision_conditions = str(
            tasks[verify_index].get("ansible.builtin.assert", {}).get("that")
        )
        self.assertIn("release_image_inspections.results | length == 4", revision_conditions)
        self.assertIn("map(attribute='item')", revision_conditions)
        self.assertIn("release_image_revision_mismatches", revision_conditions)
        self.assertEqual(
            "never",
            tasks[compose_index]["community.docker.docker_compose_v2"].get("pull"),
        )

    def test_deploy_pre_pulls_every_resolved_production_image_before_compose(self) -> None:
        tasks = playbook_tasks(ANSIBLE_ROOT / "playbooks" / "deploy.yml")
        task_names = [task.get("name") for task in tasks]
        enumerate_name = "Enumerate every resolved production image"
        validate_name = "Require every resolved production image to be immutable"
        pull_name = "Pull every immutable resolved production image"

        self.assertIn(enumerate_name, task_names)
        self.assertIn(validate_name, task_names)
        self.assertIn(pull_name, task_names)

        enumerate_index = task_names.index(enumerate_name)
        validate_index = task_names.index(validate_name)
        pull_index = task_names.index(pull_name)
        compose_index = task_names.index("Reconcile the Docker Compose project")
        self.assertLess(enumerate_index, validate_index)
        self.assertLess(validate_index, pull_index)
        self.assertLess(pull_index, compose_index)

        enumerate_task = tasks[enumerate_index]
        self.assertEqual("resolved_production_images", enumerate_task.get("register"))
        self.assertEqual(
            [
                "docker",
                "compose",
                "--project-directory",
                "{{ release_directory }}",
                "--env-file",
                "{{ release_directory }}/.env",
                "-f",
                "compose.yaml",
                "-f",
                "compose.production.yaml",
                "config",
                "--images",
            ],
            enumerate_task["ansible.builtin.command"].get("argv"),
        )

        validation_conditions = tasks[validate_index]["ansible.builtin.assert"].get(
            "that", []
        )
        self.assertIn("resolved_production_images.stdout_lines | length > 0", validation_conditions)
        self.assertTrue(
            any(
                "resolved_production_images.stdout_lines" in condition
                and "@sha256:[0-9a-f]{64}$" in condition
                for condition in validation_conditions
            )
        )

        pull_task = tasks[pull_index]
        self.assertEqual(
            "{{ resolved_production_images.stdout_lines }}", pull_task.get("loop")
        )
        self.assertEqual(
            "{{ item }}",
            pull_task["community.docker.docker_image_pull"].get("name"),
        )
        self.assertEqual(
            "always",
            pull_task["community.docker.docker_image_pull"].get("pull"),
        )

    def test_deploy_pre_pulls_the_metrics_init_image_before_compose_and_current_link(self) -> None:
        tasks = playbook_tasks(ANSIBLE_ROOT / "playbooks" / "deploy.yml")
        task_names = [task.get("name") for task in tasks]
        resolve_name = "Resolve the immutable backup metrics init image"
        pull_name = "Pull the immutable backup metrics init image"

        self.assertIn(resolve_name, task_names)
        self.assertIn(pull_name, task_names)

        resolve_index = task_names.index(resolve_name)
        pull_index = task_names.index(pull_name)
        compose_index = task_names.index("Reconcile the Docker Compose project")
        current_link_index = task_names.index(
            "Point current at the successfully reconciled release"
        )
        self.assertLess(resolve_index, pull_index)
        self.assertLess(pull_index, compose_index)
        self.assertLess(pull_index, current_link_index)

        resolved_image = tasks[resolve_index]["ansible.builtin.set_fact"].get(
            "backup_metrics_init_image_reference", ""
        )
        self.assertIn("compose.observability.yaml", resolved_image)
        self.assertIn("backup-metrics-init", resolved_image)

        observability = yaml.safe_load(
            (INFRA_ROOT.parent / "compose.observability.yaml").read_text(encoding="utf-8")
        )
        configured_image = observability["services"]["backup-metrics-init"]["image"]
        self.assertRegex(
            configured_image,
            r"^[a-z0-9]+(?:[._/-][a-z0-9]+)*(?::[a-z0-9][a-z0-9._-]{0,127})?"
            r"@sha256:[0-9a-f]{64}$",
        )

        pull_task = tasks[pull_index]
        self.assertEqual(
            "{{ backup_metrics_init_image_reference }}",
            pull_task["community.docker.docker_image_pull"].get("name"),
        )
        self.assertEqual(
            "not_present",
            pull_task["community.docker.docker_image_pull"].get("pull"),
        )

        conditions = [
            condition
            for task in tasks
            for condition in task.get("ansible.builtin.assert", {}).get("that", [])
        ]
        image_conditions = [
            condition
            for condition in conditions
            if "backup_metrics_init_image_reference" in condition
            and " is match(" in condition
        ]
        self.assertEqual(1, len(image_conditions))
        self.assertIn("@sha256:[0-9a-f]{64}$", image_conditions[0])

    def test_deploy_persists_backup_release_identity_after_compose_before_current(self) -> None:
        tasks = playbook_tasks(ANSIBLE_ROOT / "playbooks" / "deploy.yml")
        task_names = [task.get("name") for task in tasks]
        identity_name = "Install the durable backup release identity"

        self.assertIn(identity_name, task_names)
        verify_index = task_names.index(
            "Require every image revision to match the release SHA"
        )
        metrics_pull_index = task_names.index(
            "Pull the immutable backup metrics init image"
        )
        compose_index = task_names.index("Reconcile the Docker Compose project")
        identity_index = task_names.index(identity_name)
        current_index = task_names.index(
            "Point current at the successfully reconciled release"
        )
        self.assertLess(verify_index, identity_index)
        self.assertLess(metrics_pull_index, identity_index)
        self.assertLess(compose_index, identity_index)
        self.assertLess(identity_index, current_index)

        identity_task = tasks[identity_index]
        copy_args = identity_task["ansible.builtin.copy"]
        self.assertEqual("/etc/alright-mate/backup-release.env", copy_args.get("dest"))
        self.assertEqual("root", copy_args.get("owner"))
        self.assertEqual("alright-mate-backup", copy_args.get("group"))
        self.assertEqual("0640", copy_args.get("mode"))
        self.assertIs(copy_args.get("unsafe_writes"), False)
        self.assertEqual(
            "RELEASE_SHA={{ release_sha }}\n"
            "BACKUP_TOOLS_IMAGE={{ backup_image_reference }}\n",
            copy_args.get("content"),
        )

        identity_content = copy_args.get("content", "")
        for secret_name in (
            "AGE_RECIPIENT",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "DATABASE_URL",
            "GHCR_TOKEN",
            "POSTGRES_PASSWORD",
        ):
            self.assertNotIn(secret_name, identity_content)

    def test_ansible_yaml_is_parseable(self) -> None:
        yaml_paths = list(ANSIBLE_ROOT.rglob("*.yml"))
        self.assertGreaterEqual(len(yaml_paths), 5)
        for path in yaml_paths:
            with self.subTest(path=path.relative_to(INFRA_ROOT)):
                self.assertIsNotNone(yaml.safe_load(read_text(path)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
