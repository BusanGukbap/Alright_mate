# Incident runbooks

Every procedure starts by opening an incident record, preserving timestamps and release identifiers, and assigning one incident commander. Never paste credentials, tokens, private keys or database contents into chat or tickets.

## Secret rotation

1. Identify the exposed scope: database, S3, OAuth or age identity.
2. Revoke the old credential at its authority before distributing a replacement wherever continuity permits.
3. Update the approved secret store and restrictive files; do not edit repository examples with real values.
4. Recreate only affected services, verify readiness and inspect authentication failures.
5. For an age recipient change, retain old identities under offline recovery controls until every backup inside the retention window has expired or been re-encrypted and independently restored.

## Compromised OAuth

1. Disable the compromised OAuth application or client secret and revoke active sessions at the provider.
2. Remove public traffic at the Lightsail load balancer if authentication integrity cannot be guaranteed.
3. Rotate the client secret and cookie secret, validate the exact callback URL and owner/domain allow-list, then recreate the OAuth proxy.
4. Confirm unauthenticated requests fail closed and an authorised owner receives only their own data.
5. Review gateway and provider audit logs for access during the exposure window.

## Failed deploy

1. Stop further releases and capture Compose service state, health results and the deployed release SHA.
2. If the database migration completed, do not roll back application code until migration compatibility is confirmed. Never run a destructive Goose down migration during incident pressure.
3. Re-run image digest and configuration validation, then restore the last known compatible release through the normal atomic release switch.
4. Verify `/healthz`, `/readyz`, authentication and a read-only owner-scoped request before reopening traffic.

## Backup failure

1. Inspect `systemctl show alright-mate-backup.service` and the restricted service journal; do not enable shell tracing.
2. Check database readiness, DNS/TLS to the configured S3 endpoint, bucket permissions, age recipient validity and free space in tmpfs/metrics storage.
3. In object storage, ignore and later remove prefixes without `_COMPLETE`. Do not manufacture a marker.
4. Correct the root cause and start one locked manual backup. Confirm checksum metadata, final marker and success metrics.
5. If the recovery point objective is at risk, escalate immediately and preserve the most recent verified backup.

## Disk pressure

1. Confirm the affected filesystem and rate of change from node exporter; inspect container writable layers and PostgreSQL volume separately.
2. Pause non-essential builds and downloads. Do not delete PostgreSQL files, backup objects, active release data or logs needed for the incident.
3. Apply the documented retention policy to safe caches/logs, or enlarge the volume under an approved infrastructure change.
4. Verify PostgreSQL readiness, backup completion and alert recovery. Record capacity follow-up with a forecast.

## Restore drill

1. Select an explicit completed backup according to the recovery test schedule.
2. Follow `backup-and-restore.md`; verify checksum before decrypting and restore only into the disposable container.
3. Record backup ID, encrypted SHA-256, migration version, content counts, duration and outcome. Never attach the dump or identity.
4. Confirm the cleanup trap removed container and plaintext tmpfs. A failed drill is a backup incident until resolved.
