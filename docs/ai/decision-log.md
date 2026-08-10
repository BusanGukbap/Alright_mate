# AI decision log

<!-- cspell:words lockfiles socketless uninspectable unwaived -->

## Recording policy

This log stores important prompt contracts, decisions and verification outcomes. It contains **no raw transcripts**, credentials, private endpoints or unpublished user material. A proposal is evidence only after a deterministic check or an explicit human decision supports it.

AI-generated British English material is never published automatically. The separate owner-review pack remains unapproved documentation until every human gate is complete.

## Role separation

| Role | Prompt boundary | Expected evidence |
| --- | --- | --- |
| Spec drafter | Find ambiguities, edge cases and non-goals before implementation. Do not write production code. | Approved Goal, Constraints, Interfaces, Acceptance Examples, Failure Conditions, Verification and Non-goals |
| Test designer | Convert approved examples into observable failing tests without changing the requirement. | Genuine RED output and a named production break for each test |
| Implementer | Read only the approved specification and failing tests; make the smallest passing change. | Focused GREEN followed by the broader gate |
| Reviewer | Challenge the diff for specification drift, security, false evidence and unnecessary scope. | Prioritised findings with file/boundary references; no self-approval |
| British English tutor | Check reader-facing prose and explain register or spelling changes. | cspell en-GB plus owner judgement for language content |
| Operations reviewer | Threat-model trust boundaries, recovery, rollout and cleanup failure. | Executable policy tests, restore evidence and runbook corrections |

## Important prompt contracts and decisions

### 1. Contract before framework code

- **Prompt:** Define public visibility, owner lifecycle, review intervals, time-zone rules, RFC 9457 errors and non-goals before choosing handler details.
- **Adopted:** OpenAPI and feature/data specifications are the source of truth; Go and TypeScript types are generated.
- **Rejected:** Build React and Go shapes independently, then reconcile them later.
- **Human decision:** The approved product scope excludes multi-user accounts, audio, runtime AI and physical deletion.
- **Review correction:** Generated-code drift is tested from the root contract rather than inferred from a successful build.

### 2. Single-owner authentication boundary

- **Prompt:** Minimise identity data while proving that a caller cannot forge owner access.
- **Adopted:** GitHub OAuth terminates at oauth2-proxy; Nginx strips inbound identity/token headers and forwards one authentication-response user; the API checks the configured login.
- **Rejected:** Add an application user table, issue API tokens, or trust a caller-supplied OpenAPI header.
- **Executable verification:** CI uses a separate fixed-owner gateway and does not claim to emulate GitHub OAuth.
- **Review correction:** The production gateway gained query-free logging, a real private OAuth readiness probe and stricter network separation.

### 3. Deterministic review model

- **Prompt:** Turn `again` and `good` into a state machine with exact UTC/Asia/Seoul behaviour and duplicate-event semantics.
- **Adopted:** Intervals are 1, 3, 7, 14 and 30 days; `again` resets to one; a same-expression same-Korean-date repeat returns HTTP 409.
- **Rejected:** Introduce an adaptive or AI-generated schedule without a product requirement.
- **Executable verification:** Table-driven unit cases and real-PostgreSQL transaction tests cover every transition and rollback path.
- **Review correction:** Progress evidence was strengthened to show 0/0/0 → 1/0/1 → 1/1/2, not merely a non-empty chart.

### 4. Small-VM production shape

- **Prompt:** Select the least complex infrastructure that still demonstrates private networks, TLS, backup, observability and reproducible provisioning.
- **Adopted:** One Seoul Lightsail VM, Docker Compose, Nginx, OpenTofu and Ansible.
- **Rejected:** Kubernetes, Helm and Argo CD in the six-week MVP.
- **Human decision:** The cost baseline and availability trade-off are explicit; no AWS plan or apply is represented as complete.
- **Review correction:** UFW compares canonical rule sources instead of trusting a managed comment, and Docker's signing fingerprint is parsed structurally.

### 5. Immutable delivery and rollback

- **Prompt:** Deploy a reviewed commit SHA, prove image identity and retain a known-good application rollback without pretending database reversal is safe.
- **Adopted:** Three application images and the backup-tools auxiliary use SHA tags, resolved digests and matching OCI revision labels. A post-scan eligibility manifest binds the commit to the exact API, migration, web and backup-tools digests, and each idempotent delivery operation records current/previous state atomically.
- **Rejected:** `latest` tags, a caller-selected rollback SHA, automatic database rollback and an invented gateway image.
- **Executable verification:** Local policy and executable fault tests exercise eligibility resolution, pre-deployment backup observation, response-loss reconciliation, pending-operation cleanup and exact previous-digest recording.
- **Review correction:** Release failure cleanup preserves ambiguous operations for same-ID reconciliation; confirmed failure restores the recorded four-digest runtime. A dedicated forced-command SSH identity accepts only the six versioned delivery actions and cannot open an arbitrary shell.

### 6. Backup and restoration evidence

- **Prompt:** Define what makes a backup usable and make cleanup failure affect the final result.
- **Adopted:** PostgreSQL custom-format dump, age encryption, checksum, completion marker, exact metadata and disposable restore with migration/content checks.
- **Rejected:** Treating `pg_dump` exit zero or object upload alone as restoration evidence.
- **Executable verification:** The local Garage drill restored exact 1/1/1/1 migration, migration-count, expression and review-event values, and rclone reported both staging-to-final object copies as server-side.
- **Review correction:** The uninspectable MinIO client was replaced with exact distribution-patched rclone packages and env-only S3 configuration; injected work-directory cleanup failure and encrypted metadata mismatch both fail closed, and success is reported only after absence checks.

### 7. Security scan scope

- **Prompt:** Keep source, lockfiles, IaC, scripts and operational configuration covered while excluding only generated dependency/cache trees.
- **Adopted:** Digest-pinned Trivy, time-limited structured waivers and explicit manifest-root coverage.
- **Rejected:** Longer timeouts as a substitute for root-cause analysis, secret-scan exceptions, or skipping lockfiles to obtain a pass.
- **Executable verification:** Dependency findings were assigned back to their owning source inputs. Independently exported, revision-labelled API, migration, web and backup-tools candidates each passed a socketless focused Trivy scan with zero HIGH/CRITICAL findings. One fresh-cache public aggregate then covered all six declared dependency roots, workspace secrets, four configuration roots and the exact seven-image public inventory with an empty exception set; it completed in about 148 seconds with zero HIGH/CRITICAL findings and no residual labelled container.
- **Review correction:** Mount-scoped skip paths replaced ineffective relative paths after debug evidence showed traversal into `.tools`, and the API build stage moved from Go 1.26.3/Alpine 3.23 to the reviewed Go 1.26.5/Alpine 3.24 digest.

### 8. Responsive acceptance

- **Prompt:** Measure page-level overflow at wide desktop and 320 pixels; permit the progress table to scroll only within its labelled container.
- **Adopted:** Browser measurements and a persisted Playwright regression accompany the CSS fix.
- **Rejected:** Judging responsiveness from a desktop screenshot alone.
- **Executable verification:** Home, Browse, Admin and Review each measured zero page overflow at 320 pixels; the review container measured 283 pixels client width and 544 pixels scroll width.
- **Review correction:** Removed the root minimum width and allowed direct page-stack children to shrink with `min-width: 0`.

## Review corrections that changed implementation

- Public `q` and `tag` filters were proved as an AND intersection with query-only and tag-only controls.
- Sparse owner edits can explicitly clear optional fields; form errors, focus and rapid-submit behaviour are accessible and deterministic.
- Playwright retries truncate and reseed inside each attempt, so a failed mutation cannot contaminate the next run.
- Production application images reject mutable references and mismatched OCI revisions before Compose mutation.
- Grafana and cAdvisor were removed from the MVP runtime after their newest reviewed official images retained unwaived HIGH findings. Private Prometheus still covers API, host and backup signals without exposing a Docker socket; richer dashboards and per-container metrics are deferred.
- The narrow review table scroll is contained without reintroducing page-level overflow.

## Human publication gate

The twelve candidates in `docs/content-review.md` are not seed data, not owner-approved and not published. The owner must check the cited sense, Korean meaning, original example and tags for each item. An AI suggestion can help draft a candidate, but it cannot tick those boxes or call a product API.
