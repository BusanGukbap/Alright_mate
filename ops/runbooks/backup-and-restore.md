# Encrypted backup and restore runbook

## Safety properties

The backup process uses PostgreSQL 18 `pg_dump --format=custom`. Dump bytes travel through a pipe directly into age; no unencrypted dump is written to persistent storage. The work directory is container tmpfs with mode `0700`. Object publication is considered complete only when both encrypted objects exist beneath the final backup prefix and `_COMPLETE` has been uploaded last. Restore ignores incomplete prefixes.

Metadata is encrypted separately and records the UTC timestamp, deployed release SHA, applied Goose migration version and migration count, exact non-zero expression and review-event counts, retention days, ciphertext SHA-256 and format. The release SHA and age recipient are injected. Credentials are neither arguments nor log fields.

The single-node Garage profile is only a local S3-compatible integration harness. Local elapsed-lifecycle API coverage is not claimed: the immediate drill proves encrypted Put, rclone-reported server-side Copy, completion marker publication, Get, checksum verification and isolated restore, but it does not wait for expiration. Production retention remains an independently enforced S3 lifecycle/versioning policy with its own infrastructure contract.

The S3 client is configured entirely through `RCLONE_CONFIG_OPS_*` environment variables with `RCLONE_CONFIG=/dev/null`; no rclone credential file is created. The production AWS S3 bucket uses `S3_ENDPOINT=https://s3.ap-northeast-2.amazonaws.com` and `S3_REGION=ap-northeast-2`. Production endpoints must use HTTPS; only the private `http://garage:3900` integration endpoint is accepted without TLS.

## One-time production preparation

These operational preparation steps require change approval. They do not run OpenTofu or perform AWS actions. The checked-in example remains non-functional because its bucket and credential values are placeholders:

1. Create `alright-mate-backup` as a locked, non-interactive service account. Give it read-only access to the release and the minimum Docker access needed to launch the pre-defined backup service. Membership of the Docker group is root-equivalent; audit it and do not permit interactive login.
2. Copy `ops/backup/backup.env.example` to `/etc/alright-mate/backup.env`, replace every placeholder through the approved secret store, set owner `root:alright-mate-backup`, and set mode `0640`. This static secret file must not contain `RELEASE_SHA` or `BACKUP_TOOLS_IMAGE`.
3. Generate the age identity on a controlled operator workstation. Store the private identity in the secrets system and offline recovery custody. Put only its public recipient in `backup.env`. Never copy the private identity into the repository or routine backup host environment.
4. After a separately approved OpenTofu apply, set `S3_BUCKET` from the `backup_bucket_name` OpenTofu output. The module provisions the private regional AWS S3 bucket, versioning, the retention lifecycle from `backup_retention_days`, and default encryption with a customer-managed AWS KMS key. It does not provision the IAM identity or its credentials; use only already-approved scoped IAM credentials with explicit S3 object and KMS data-key permissions. Set `S3_ENDPOINT=https://s3.ap-northeast-2.amazonaws.com` and `S3_REGION=ap-northeast-2`, and confirm `BACKUP_RETENTION_DAYS` matches the reviewed OpenTofu input.
5. Install the systemd service and timer from `ops/backup/systemd/`, then run `systemd-analyze verify` and `systemctl daemon-reload`. Do not enable the timer until the first successful immutable deployment has created `/etc/alright-mate/backup-release.env`.

Ansible owns `/etc/alright-mate/backup-release.env`. It contains only the current `RELEASE_SHA` and immutable `BACKUP_TOOLS_IMAGE`, is replaced atomically after successful reconciliation, and is restored with the previous release during rollback. Operators must not copy these values into the static secret file. A pre-deployment backup uses a later transient `/run/alright-mate-backup-release.env` drop-in for the exact previous release; the delivery controller removes that override after every success or failure so the nightly timer returns to the durable current identity. The unit passes `--env-file /dev/null` deliberately: the backup account may traverse the release configuration but must not read the application's mode-`0600` `.env`; all backup inputs arrive through the two reviewed systemd environment files instead.

Backup metrics use the private `backup-metrics` Docker volume. Before every backup, the root-only `backup-metrics-init` service makes its root directory UID/GID 10001 and mode `0755`; backup then writes as UID 10001 and node exporter mounts the same volume read-only. Do not replace it with an unverified host bind mount.

## Manual backup and evidence

Before enabling the timer, run the same service once:

```sh
sudo systemctl start alright-mate-backup.service
sudo systemctl show alright-mate-backup.service -p Result -p ExecMainStatus
sudo journalctl -u alright-mate-backup.service --since today
```

A successful log line contains only `backup_result=success`, the non-secret backup ID and duration. Confirm the object prefix contains `database.dump.age`, `metadata.json.age` and `_COMPLETE`. Do not log or download plaintext. The `backup-metrics` volume must contain last-attempt, last-run-success and last-success metric files, owned by UID/GID 10001 and readable by node exporter.

## Restore drill

Use a named backup selected from a completed object prefix. Never accept “latest” implicitly.

1. Obtain change approval and record the selected backup ID and incident/drill ticket.
2. Retrieve the age identity from the secret store into a restrictive temporary host file, owner-only mode `0600`. The root-only `restore-secret-init` service copies it to the private `restore-secrets` volume as UID/GID 10001, mode `0400`, before restore can start. Determine the Compose backend network and Docker socket group ID.
3. Export the production S3 variables, including `S3_REGION`, without echoing their values. Run the manual profile:

```sh
export AGE_IDENTITY_FILE=/run/operator/alright-mate-age-identity
export RESTORE_NETWORK=alright-mate_backend
export DOCKER_GID="$(stat -c %g /var/run/docker.sock)"
docker compose -f compose.yaml -f compose.production.yaml -f compose.observability.yaml \
  --profile restore run --rm restore-drill 'EXPLICIT_BACKUP_ID'
```

The drill verifies `_COMPLETE`, decrypts metadata, checks the encrypted dump SHA-256, decrypts and validates the custom-format archive, and starts a disposable PostgreSQL 18 container with no published ports. It restores with `--exit-on-error` and requires the restored applied-migration count, migration version, expression count and review-event count to be non-zero exact matches for the encrypted source metadata. Its exit handler verifies both the disposable database and plaintext tmpfs work directory are absent; either cleanup failure makes the whole drill fail.

4. Confirm `restore_drill_result=success`, `cleanup_container_absent=1` and `cleanup_workdir_absent=1`; store the non-secret checksum and exact counts in the drill record, and independently confirm no `alright-mate-restore-*` container remains.
5. Remove the temporary identity file using the approved secure deletion process. Revoke the temporary operator access.

The restore drill never accepts a live database URL and must never overwrite the production database. A production recovery is a separate, explicitly approved procedure after an isolated drill succeeds.

## Backup failure

See the **backup failure** procedure in `incidents.md`. Do not weaken checksum, encryption, retention or completion-marker checks merely to make a run succeed.
