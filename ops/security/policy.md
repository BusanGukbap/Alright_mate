# Vulnerability and configuration scanning policy

Run `ops/security/scan.sh` before every production release. It rejects an empty, partial, duplicate, mutable or stale `TRIVY_IMAGES` value before Docker is invoked. `runtime-image-inventory.json` is checked against every `image:` line in the base, production, observability and TLS-bootstrap Compose sources; tag-plus-digest source values are normalised to `name@digest`, and the caller must supply the exact deterministic set of seven literal public images. The gate also validates `dependency-scan-roots.json` against every Go, npm and Python dependency manifest in the repository. An undeclared, stale or duplicate manifest fails before scanning.

The wrapper downloads the vulnerability database once, scans each declared manifest root for vulnerabilities without network access, scans the workspace once for secrets, checks `infra/`, `ops/`, `apps/api/` and `apps/web/` configuration, then scans every listed immutable public image. Each Trivy process has a 120-second scanner and host bound; the complete wrapper has a 600-second bound. Exact run-labelled containers are removed on success, failure, signal or timeout. The executable Compose contract suite separately validates the merged Compose model because Trivy 0.73 does not treat Compose YAML as a supported misconfiguration target.

The gate fails on every HIGH or CRITICAL finding, including unfixed findings. A security owner may add a narrow exception only when immediate remediation is riskier than temporary acceptance. Each `.trivyignore.yaml` entry must include:

- the exact vulnerability or misconfiguration identifier;
- one or more exact, wildcard-free affected `paths`: repository-relative for filesystem/IaC findings and image-root-relative for image findings;
- an accountable `owner=<team-or-person>` field in `statement`;
- a concrete `reason=<temporary-risk-acceptance>` field in `statement`;
- a specific `compensating_control=<enforced-control>` field in `statement`;
- `expired_at` as an RFC3339 UTC timestamp between one and thirty calendar days ahead.

`ops/security/validate_trivy_policy.py` validates that structure before every scan. Vulnerability identifiers must be exact CVE or GHSA values; wildcard identifiers and paths fail. Expired, same-day and over-thirty-day exceptions fail and must be removed or reviewed; they must never be extended silently. Secret-scan exceptions are forbidden, and licence exceptions are outside this policy.

The current exception file is empty. A patched immutable upstream image must be preferred to an exception, and any temporary exception must be removed as soon as a fixed release is available.

## Generated-directory scan boundary

The filesystem gate scans source, Dockerfiles, IaC, scripts, operational configuration and dependency lockfiles. Its exact `skip-dirs` allow-list excludes only generated non-source or local session-state material:

- `.Codex` is gitignored, non-release Codex session state. It may contain disposable local scan exports that must not be traversed; executable contracts prove that it contains no tracked file or declared dependency root before permitting this directory-level exclusion;
- `.tools` contains workspace-local Go/OpenTofu toolchains and caches;
- `**/node_modules` contains reproducibly installed package trees whose lockfiles remain scanned;
- `**/dist` contains compiled frontend output whose source and build configuration remain scanned;
- `**/.terraform` contains downloaded provider/module caches while every Terraform/OpenTofu source and lockfile remains scanned;
- `.git` is version-control object metadata, not the checked-out release source;
- `playwright-report` and `test-results` are disposable browser-test artefacts.

The contract suite requires this exact set and forbids `skip-files`, so a source directory or lockfile cannot be silently removed from the gate. It also proves that application, infrastructure, operations, specification and documentation paths remain covered, and that `.superpowers` is not pruned or skipped. Any change to the allow-list requires a security review and a size/evidence update.

Trivy 0.73 requires these values under `scan.skip-dirs` and the unfixed-finding choice under `vulnerability.ignore-unfixed`. The contract suite forbids the older document-root forms because Trivy accepts but ignores them.

## Dependency-root coverage

`dependency-scan-roots.json` is executable policy, not a hand-maintained checklist. The validator discovers supported manifests after pruning only the reviewed generated/session-directory allow-list and requires an exact match with these roots. A behavioural contract proves that a synthetic manifest under `.Codex` is pruned while the same manifest under `.superpowers` remains discoverable and fails closed unless declared:

- `apps/api` and `apps/api/tools/goose` for the two Go modules;
- `apps/web` for the production npm dependency graph;
- `infra/ansible` and `tests/delivery` for pinned Python requirements;
- `tests/e2e` for the browser-test npm lockfile.

Trivy's default npm policy suppresses development/test dependencies. This is intentional for `tests/e2e`: those packages are exactly pinned, execute only in CI and are not copied into a production image. The E2E root is still run and must remain declared, but a clean result is not represented as runtime-package coverage. The production web root retains runtime package analysis while build-only packages are suppressed for the same non-shipping reason.

There is no residual workspace vulnerability scan after all declared roots pass. Once the exact manifest inventory is proved, files outside those roots contain no supported dependency input and add no package coverage. Source, scripts and configuration outside dependency roots remain covered by the workspace secret scan and the explicit IaC scans.

## Runtime-image boundary

`runtime-image-inventory.json` classifies every live Compose image reference rather than silently treating a sample image as runtime coverage:

- seven deduplicated literal public images are scanned by this wrapper and must be passed in the inventory's sorted `name@digest` order;
- `API_IMAGE`, `MIGRATION_IMAGE` and `WEB_IMAGE` are authenticated application references whose exact published digests are scanned by the protected delivery workflow before its eligibility marker is created;
- `BACKUP_TOOLS_IMAGE` is a distinct authenticated operational auxiliary. The delivery workflow must build, scan and revision-bind its immutable digest, and Ansible must verify that digest before operations use it;
- `IMAGE_TAG` occurs only on the three local build references and is classified as local-only, never as production scan evidence.

The ordinary public-image aggregate intentionally does not claim authenticated or locally built image coverage. Complete release evidence is the combination of this exact public set and the delivery workflow's authenticated scans for all three application images plus the backup-tools auxiliary. Production service configuration must set `BACKUP_TOOLS_IMAGE` to the revision-bound immutable digest; its Compose local-build fallback is never production coverage.

The current OAuth boundary is a temporary exact digest from the separate official `quay.io/oauth2-proxy/oauth2-proxy-nightly` repository, not a mutable-tag exception or vulnerability waiver. It must be replaced by the next clean official stable digest. Runtime automation checks configuration and `/ready`; an actual GitHub OAuth sign-in remains a required manual post-deployment smoke test.

Example invocation for image scanning (all values are public image references):

```sh
TRIVY_CACHE_VOLUME='alright-mate-trivy-cache' \
TRIVY_IMAGES="$(python3 ops/security/validate_runtime_image_inventory.py \
  --repo-root . \
  --inventory ops/security/runtime-image-inventory.json \
  --print-public-images)" \
  ops/security/scan.sh
```

The cache volume is reusable state owned by the caller or CI cache lifecycle. The wrapper never deletes it implicitly. `TRIVY_SCAN_RUN_ID` is optional; when supplied it must contain only letters, digits, dots, underscores or hyphens and should be unique to the CI run.
