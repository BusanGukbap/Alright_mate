# Alright Mate operations

This directory contains the single-VM operational controls for encrypted backup and restore, private monitoring, vulnerability scanning and incident response. It deliberately contains no credentials, private keys or production endpoints.

## Components

- `backup/backup.sh` streams a PostgreSQL 18 custom-format dump directly through age encryption, publishes encrypted objects through a staging prefix, and writes the `_COMPLETE` publication marker last.
- `backup/restore-drill.sh` accepts one explicit backup ID, verifies and decrypts it in container tmpfs, restores it to a disposable PostgreSQL 18 container and removes all temporary state.
- `monitoring/` provisions Prometheus rules. The checked-in Grafana dashboard and provisioning files are inactive future assets: Compose does not mount or run them until an official Grafana image passes the unchanged security gate. The overlay publishes no monitoring port to the host and gives monitoring no Docker socket access.
- Root-only one-shot initialisers materialise the restore identity with the exact container UID and prepare the UID-10001 backup-metrics volume before non-root services start.
- `security/` defines the bounded Trivy policy, proves complete dependency-manifest and runtime-image coverage, requires the exact seven public image digests and structurally enforces time-limited exceptions. Delivery separately scans three authenticated application images and the revision-bound backup-tools auxiliary.
- `runbooks/` contains installation, access and incident procedures.

The overlay is additive:

```sh
docker compose -f compose.yaml -f compose.production.yaml -f compose.observability.yaml config
docker compose -f compose.yaml -f compose.production.yaml -f compose.observability.yaml --profile observability up -d
```

Production activation, firewall changes and object-store credentials remain operator-owned external steps. The digest-pinned Garage `backup-test` profile is an isolated, single-node local harness only; it is not the production object-store recommendation.

Local elapsed-lifecycle API coverage is not claimed. The local drill preserves encrypted Put, server-side Copy, completion marker publication, Get, checksum verification and isolated restore evidence. Production retention remains an independently enforced S3 lifecycle/versioning policy and is not delegated to the local emulator.

The backup-tools image uses the distribution-patched `rclone` S3 client instead of the removed MinIO client. Its `Other` provider, path-style endpoint, region and scoped credentials are supplied only through `RCLONE_CONFIG_OPS_*` environment variables while `RCLONE_CONFIG=/dev/null`; no client credential file is created. The scripts accept HTTPS endpoints, with `http://garage:3900` as the sole private local-drill exception. See the official [rclone environment-variable contract](https://rclone.org/docs/#environment-variables) and [S3 backend options](https://rclone.org/s3/).

## Runtime security pins and upgrade boundary

The greenfield runtime uses the exact digest-pinned Chainguard PostgreSQL 18.4 image. Its entrypoint receives only the bounded initialisation capabilities needed to prepare a fresh named volume and drop privileges; the steady-state PostgreSQL processes run as UID 70. PostgreSQL remains private with no host-published database port.

An existing PostgreSQL 17 volume must not be mounted in place as PostgreSQL 18 storage, deleted or mutated by this change. An operator upgrading a real PostgreSQL 17 environment must take and verify a compatible logical dump, restore it into a new PostgreSQL 18 volume, perform the required reindex and collation-version refresh checks, validate the restored service, and retain the old volume throughout the approved rollback window. This implementation created only disposable test volumes and did not migrate or delete user data.

The OAuth gateway temporarily uses the immutable digest from the separate official `oauth2-proxy-nightly` repository, labelled `v7.15.3-9-g14af2951`, because it passed the unchanged HIGH/CRITICAL gate while the assessed stable image did not. Replace it with the next official stable immutable image that passes the same gate. The automated checks prove configuration parsing and listener readiness only; real GitHub OAuth remains a manual post-deployment smoke test.
