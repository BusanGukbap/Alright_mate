# Operations Contract

## Health, readiness and metrics

`GET /healthz` is a liveness check: the process can serve HTTP and returns 200 with `status: ok`; it does not require a database query. `GET /readyz` is a readiness check: required dependencies, including PostgreSQL, are usable and it returns 200 only then. A not-ready service returns 503 as an RFC 9457 problem document.

The application exposes Prometheus-format metrics only on a private endpoint or private network listener. Required signals are HTTP request count and duration, database readiness, due-review count, review-event count and application build information. Metrics are not a public product API.

### Security-constrained observability deviation

The original completion evidence also called for Grafana dashboards and container-resource health. As of 9 August 2026, the newest reviewed official Grafana and cAdvisor images retained unwaived HIGH findings, so the zero-unwaived-finding policy excludes both from the runnable MVP. The current local handoff keeps private Prometheus, API metrics, node_exporter host metrics and backup metrics; it does not claim Grafana dashboards or per-container CPU and memory panels.

This proposed boundary remains pending owner approval rather than silently changing the approved goal. Overall acceptance remains pending until the owner accepts the deviation or a newly reviewed clean upstream image restores the original evidence.

## Backup and restore

PostgreSQL is the source of record. Automated backups produce encrypted, timestamped PostgreSQL logical dumps in object storage with retention configured by deployment. A backup is usable only after checksum verification and a restore drill to an isolated PostgreSQL instance.

Restore is an operator-controlled procedure: announce maintenance, stop writes, select and verify the intended backup, restore into an isolated target, run schema and row-count checks, switch only after approval, then record the drill outcome. Restoration never silently overwrites a live database.

## Deployment and rollback

MVP production runs on a Seoul VM using Docker Compose. Nginx is the only process with host-published ports; API and PostgreSQL stay on a private Compose network. Release deployment pulls immutable image references, runs migration checks before serving the new version, verifies `/healthz` and `/readyz`, and preserves the previous release reference.

Rollback selects the previous known-good immutable image, evaluates migration compatibility before application rollback, restarts the private services, and rechecks health, readiness and public read behaviour. A destructive database rollback is not part of ordinary application rollback; restoration follows the separate restore procedure.

## Operational boundaries

TLS termination and oauth2-proxy sit at the gateway. The owner identity header is stripped from incoming client traffic and set only after gateway authentication. Kubernetes is explicitly a post-MVP consideration, not an operational dependency.
