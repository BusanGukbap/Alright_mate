# Alright, Mate?

<!-- cspell:words healthz readyz Redocly sqlc unwaived workflow_dispatch -->

Alright, Mate? is a single-owner British English expression journal built as an end-to-end DevOps portfolio project. Public readers can browse sourced expressions; the owner can curate them and practise a deterministic spaced-review queue. React, Go and PostgreSQL remain separate components behind an Nginx gateway, and the repository carries the specifications, tests, infrastructure and runbooks needed to explain every boundary.

There is no public live URL yet. The application and its disposable acceptance environment are **verified locally**; cloud creation and production smoke tests remain **pending owner-authorised work**.

## What is implemented

- Public browse, search, tag filtering and expression detail routes.
- Single-owner draft, publication and archive flows through a trusted gateway header.
- `again` / `good` review scheduling with Asia/Seoul date boundaries and RFC 9457 errors.
- Reproducible local and CI Compose modes, plus a production OAuth boundary.
- OpenTofu and Ansible definitions for a Seoul Lightsail VM.
- Scan-gated, exact-digest delivery and rollback controllers with idempotent operations.
- Encrypted backup, a disposable restore drill and private Prometheus plus node_exporter monitoring. Grafana and cAdvisor are deferred until clean upstream images are available.
- Specification-driven development records and separated AI review roles.

The request path is `Browser → Nginx → oauth2-proxy for owner routes → Go API → PostgreSQL`. The gateway is the only service with a host port; the API and PostgreSQL have no host-published ports.

## Prerequisites

The supported contributor environment is WSL2 Ubuntu with Docker Desktop integration enabled.

| Tool | Reviewed version or range | Used for |
| --- | --- | --- |
| GNU Make and Bash | Current WSL2 Ubuntu packages | Root command interface |
| Docker Engine with Compose v2 | Local evidence used Engine 28.3.3 | Runtime and PostgreSQL integration tests |
| Go | 1.26.x; container builder 1.26.5 | API generation, tests and build |
| Node.js | 22.12.0 | React, cspell and Playwright |
| Python | 3.11 | Contract, infrastructure and operations tests |
| OpenTofu | 1.12.1 | Static infrastructure validation |
| Ansible | `ansible-core` 2.18.18 and `ansible-lint` 25.6.1 | Host and release policy validation |
| GitHub CLI (`gh`) | Current supported release, authenticated to the repository | Enqueueing protected production workflows only |

Docker must be reachable from WSL. Docker-required targets stop with a clear error when the daemon is unavailable; they never turn a skipped integration suite into a pass.

## Clean-clone local start

The product start needs Docker and Make only; application toolchains are installed inside the image builds.

```sh
git clone <repository-url>
cd alright-mate
cp .env.example .env
make dev
```

Open [http://localhost:8080](http://localhost:8080). The local gateway also exposes `/expressions`, `/admin`, `/review`, `/healthz` and `/readyz` on that origin. Local owner access is a deliberate development-only trust mode; production uses GitHub OAuth through oauth2-proxy.

Stop the local stack, including its project network and containers, with:

```sh
docker compose -f compose.yaml -f compose.local.yaml down --remove-orphans
```

The named PostgreSQL volume is retained. Add `--volumes` only when you deliberately want to discard local data.

## Contributor set-up

Install locked host dependencies before generation, local checks or E2E:

```sh
cd apps/api && go mod download && cd ../..
npm ci --ignore-scripts --prefix apps/web
npm ci --ignore-scripts --prefix tests/e2e
python3 -m pip install --requirement tests/delivery/requirements.txt
python3 -m pip install --requirement infra/ansible/requirements.txt
ansible-galaxy collection install --requirements-file infra/ansible/requirements.yml
```

Playwright also needs its reviewed Chromium build before the browser suite:

```sh
cd tests/e2e && npx playwright install --with-deps chromium && cd ../..
```

## Root commands

| Command | Contract |
| --- | --- |
| `make dev` | Build and start the local gateway, web, API, migration and PostgreSQL services, then wait for health. |
| `make generate` | Regenerate Go OpenAPI/sqlc artefacts and the TypeScript OpenAPI schema. |
| `make openapi-lint` | Run the exact pinned Redocly OpenAPI lint used by CI. It may download that pinned CLI through npm. |
| `make check` | Run locally safe generation drift, OpenAPI lint, portfolio, real-PostgreSQL backend, frontend, runtime-policy, delivery-policy and en-GB spelling gates. Docker is mandatory. |
| `make e2e` | Build a fresh disposable stack and run the real Chromium public, owner and review journeys. |
| `make infra-check` | Validate OpenTofu and Ansible without an AWS backend, plan or apply. It may install/download the pinned provider if it is not cached. |
| `make delivery-lint` | Run pinned actionlint and the digest-pinned ShellCheck container for delivery workflows and scripts. It may download those pinned tools. |
| `make security-check` | Derive the exact public runtime-image inventory and run the bounded, fail-closed Trivy gate. It downloads the vulnerability database and may pull missing reviewed images. |
| `make deploy RELEASE_SHA=<sha>` | Enqueue `deploy.yml` on `main` with an authenticated GitHub CLI. It may change a production host only after the external `production` Environment has required reviewers configured and one approves the job. |
| `make rollback` | Enqueue `rollback.yml` on `main`; after the same externally configured approval, the workflow selects the controller-recorded previous immutable release. |
| `make backup` | Run one encrypted backup against the active Compose database and the explicitly supplied object-store endpoint. This uploads data. |
| `make restore-drill` | Create a local encrypted backup and prove restoration in a disposable PostgreSQL container, including cleanup and failure paths. |

Security scanning remains separate as `make security-check`; the target derives the exact public runtime-image inventory in the same shell, downloads/uses the pinned Trivy database and fails on unwaived HIGH or CRITICAL findings. `make check` never applies AWS resources, changes DNS or firewalls, contacts a production host, publishes a package, uploads a backup or deploys a release.

The two delivery Make targets call GitHub's manual `workflow_dispatch` interface; they do not run the SSH controller locally. Publication creates a release eligibility manifest only after scanning the three application images and the backup-tools auxiliary. Deployment accepts that commit-bound marker, uses its exact API, migration, web and backup-tools digests, and assigns an idempotent operation so a lost response can be reconciled without choosing mutable tags again. A separate forced-command SSH identity can invoke only the six versioned delivery actions; it cannot run an arbitrary remote shell.

Generation is expected to be deterministic. `make check` hashes the generated artefacts before and after generation and fails if they change. Run `make generate` deliberately, review the diff, then run `make check` again.

## Evidence and pending production work

The local acceptance suite has exercised public browse, owner lifecycle, review scheduling, duplicate-review conflict handling and gateway header boundaries against real PostgreSQL. The final responsive regression runs six Chromium journeys; at 320 pixels all four product routes have zero page-level horizontal overflow, while the wide review table scrolls only inside its labelled container. Frontend unit tests, type checking, lint, en-GB spelling and production build also pass locally.

The following are not claimed as complete:

- AWS apply and any paid Lightsail or object-storage creation.
- DNS delegation and TLS issuance for a real domain.
- GitHub OAuth registration and production OAuth smoke testing.
- Pending configuration of protected-branch rules and the `production` Environment's required reviewers and secrets.
- GHCR publication from protected `main`.
- A production deployment, production rollback and live traffic checks.
- Production object-store backup and restore evidence.
- Owner acceptance of the reduced Prometheus/node_exporter monitoring boundary, or newly reviewed clean Grafana and cAdvisor images.
- Owner review and publication of the twelve sourced content candidates.

The content pack in [docs/content-review.md](docs/content-review.md) contains twelve sourced candidates, not twelve approved public entries. Every owner gate is unchecked; nothing in that document is seed data or an automatic publication input.

## Portfolio map

- [Architecture, traceability and trade-offs](docs/portfolio.md)
- [AI decision log](docs/ai/decision-log.md)
- [Content owner-review pack](docs/content-review.md)
- [Product specification](docs/specs/000-product.md)
- [OpenAPI contract](specs/openapi.yaml)
- [Infrastructure guide](infra/tofu/README.md)
- [Operations guide](ops/README.md)
- [Backup and restore runbook](ops/runbooks/backup-and-restore.md)

No runtime LLM API, multi-user account system, Kubernetes, Helm or Argo CD component is part of this MVP.
