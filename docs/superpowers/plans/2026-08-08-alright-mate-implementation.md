# Alright, Mate? Implementation Plan

> **For agentic workers:** Use subagent-driven development. Do not commit, push or publish changes; the user requires separate approval for every Git publication action.

**Goal:** Deliver a reproducible full-stack British English learning journal with public browsing, owner administration, spaced review, automated delivery and operational evidence.

**Architecture:** A Vite React SPA and Go HTTP API communicate through an OpenAPI contract. PostgreSQL is the system of record. Nginx is the sole public gateway, with oauth2-proxy protecting owner paths. Docker Compose runs the service on a Seoul Lightsail VM provisioned with OpenTofu and configured with Ansible.

**Tech Stack:** React, TypeScript, Vite, Go, chi, pgx, sqlc, goose, PostgreSQL, OpenAPI, Docker Compose, Nginx, oauth2-proxy, OpenTofu, Ansible, GitHub Actions, Prometheus, Grafana, Trivy and Playwright.

## Global Constraints

- All UI copy, specs, ADRs, runbooks and README prose must use British English.
- No Java, runtime LLM, multi-user support, audio, Kubernetes, Helm or Argo CD.
- Feature specs, contracts and failing tests precede production code.
- Only Nginx exposes host ports; production database and API ports remain private.
- Owner identity is the configured GitHub login supplied by oauth2-proxy through a stripped and trusted header.
- Public responses include published expressions only.
- Publishing requires a valid source URL.
- Review timestamps use UTC; review dates use Asia/Seoul.
- Review intervals are exactly 1, 3, 7, 14 and 30 days. Again resets to 1; Good advances and caps at 30.
- Duplicate same-expression, same-Korean-date reviews return 409.
- API errors follow RFC 9457.
- Codex does not commit, push or create a pull request without explicit user approval.

---

### Task 1: SDD contracts and repository foundation

**Produces:** Product and feature prompt contracts, OpenAPI 3.1 contract, SQL-oriented data contract, ADRs, British English language policy and repository structure decision.

**Verification:** OpenAPI parses and all feature requirements trace to an operation, schema or explicit non-goal. No production code is created.

### Task 2: Go and PostgreSQL API

**Produces:** Database migrations, generated/query layer, review scheduling domain logic, public expression reads, owner CRUD, due reviews, review submission, progress summary, health/readiness and Prometheus metrics.

**Verification:** Unit and PostgreSQL integration tests prove visibility, validation, review advancement, duplicate conflict, authentication boundary and RFC 9457 responses.

Implementation gates:

- **2A — Domain and schema:** Go module, review state machine and initial migration.
- **2B — Storage and application:** sqlc queries, repositories and atomic use cases with PostgreSQL integration tests.
- **2C — HTTP contract:** oapi-codegen v2.8.0 models/Chi server, handlers, trusted-owner boundary, probes and metrics.

### Task 3: React application

**Produces:** Public home/list/detail routes, search and tag filtering, admin create/edit/status flows, due-review interaction and thirty-day progress UI using generated OpenAPI types.

**Verification:** Vitest and Testing Library cover loading, empty, error and successful behaviour; cspell validates en-GB copy.

### Task 4: Runtime, authentication and E2E

**Produces:** Local and production Compose configurations, Nginx gateway, TLS integration points, oauth2-proxy configuration, production-safe trusted headers, container images and Playwright journeys.

**Verification:** Compose config validation, container health checks, public/admin route isolation and Playwright public, CRUD and review flows.

### Task 5: Infrastructure, delivery and operations

**Produces:** OpenTofu for Lightsail, static IP, firewall, DNS and object storage; Ansible provisioning and release playbooks; GitHub Actions validation/build/deploy; backup/restore; Prometheus/Grafana; Trivy and host hardening.

**Verification:** Offline validation where credentials are not required. Real AWS apply, OAuth registration, DNS delegation and paid-resource creation require explicit user approval and credentials.

**Recorded deviation, pending owner approval (9 August 2026):** private Prometheus, API metrics, node_exporter and backup metrics remain active, while Grafana and cAdvisor are excluded because their newest reviewed official images retained unwaived HIGH findings. This does not satisfy the original Grafana/container-health completion evidence unless the owner accepts the reduced boundary or clean upstream images are introduced through a new review cycle.

### Task 6: Acceptance and portfolio handoff

**Produces:** Make targets, clean-clone instructions, architecture and SDD trace, AI decision log, operations runbooks, security notes, trade-offs, final review findings and Kubernetes follow-up backlog.

**Verification:** All locally executable checks pass. External checks that need AWS, a domain or GitHub OAuth are documented as pending user-authorised smoke tests rather than reported as complete.
