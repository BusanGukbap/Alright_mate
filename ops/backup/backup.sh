#!/bin/sh
# shellcheck shell=busybox
set -eu
set -o pipefail

umask 077

: "${AGE_RECIPIENT:?AGE_RECIPIENT is required}"
: "${DATABASE_URL:?DATABASE_URL is required}"
: "${RELEASE_SHA:?RELEASE_SHA is required}"
: "${RETENTION_DAYS:?RETENTION_DAYS is required}"
: "${S3_ACCESS_KEY:?S3_ACCESS_KEY is required}"
: "${S3_BUCKET:?S3_BUCKET is required}"
: "${S3_ENDPOINT:?S3_ENDPOINT is required}"
: "${S3_REGION:?S3_REGION is required}"
: "${S3_SECRET_KEY:?S3_SECRET_KEY is required}"

case "$AGE_RECIPIENT" in
  age1*) ;;
  *) echo "backup configuration error: AGE_RECIPIENT is not an age recipient" >&2; exit 64 ;;
esac
case "${AGE_RECIPIENT#age1}" in
  *[!0-9a-z]*|'') echo "backup configuration error: AGE_RECIPIENT is not an age recipient" >&2; exit 64 ;;
esac
case "$RELEASE_SHA" in
  *[!0-9a-f]*|'') echo "backup configuration error: RELEASE_SHA must be lower-case hexadecimal" >&2; exit 64 ;;
esac
if [ "${#RELEASE_SHA}" -lt 7 ] || [ "${#RELEASE_SHA}" -gt 64 ]; then
  echo "backup configuration error: RELEASE_SHA must contain 7 to 64 hexadecimal characters" >&2
  exit 64
fi
case "$RETENTION_DAYS" in
  *[!0-9]*|'') echo "backup configuration error: RETENTION_DAYS must be a positive integer" >&2; exit 64 ;;
esac
if [ "$RETENTION_DAYS" -lt 1 ]; then
  echo "backup configuration error: RETENTION_DAYS must be at least one" >&2
  exit 64
fi
case "$S3_BUCKET" in
  *[!0-9a-z.-]*|'') echo "backup configuration error: S3_BUCKET is invalid" >&2; exit 64 ;;
esac
case "$S3_ENDPOINT" in
  https://*|http://garage:3900) ;;
  *) echo "backup configuration error: S3_ENDPOINT must use HTTPS (except the private local Garage harness)" >&2; exit 64 ;;
esac
case "$S3_REGION" in
  *[!0-9A-Za-z._-]*|'') echo "backup configuration error: S3_REGION is invalid" >&2; exit 64 ;;
esac

workdir=$(mktemp -d /work/backup.XXXXXX)
backup_id=""
object_stage=""
remote_stage=""
rclone_ready=0
success=0
started_at=$(date +%s)
attempted_at="$started_at"

write_metric_file() {
  metric_name=$1
  metric_value=$2
  [ -n "${BACKUP_METRICS_DIR:-}" ] || return 0
  metric_tmp="${BACKUP_METRICS_DIR}/.${metric_name}.$$"
  printf '# TYPE %s gauge\n%s %s\n' "$metric_name" "$metric_name" "$metric_value" >"$metric_tmp"
  chmod 0644 "$metric_tmp"
  mv -f "$metric_tmp" "${BACKUP_METRICS_DIR}/${metric_name}.prom"
}

cleanup() {
  status=$?
  trap - EXIT INT TERM
  set +e
  if [ "$success" -eq 1 ] && [ "$status" -eq 0 ]; then
    write_metric_file alright_mate_backup_last_run_success 1
    write_metric_file alright_mate_backup_last_success_timestamp_seconds "$attempted_at"
  else
    write_metric_file alright_mate_backup_last_run_success 0
  fi
  write_metric_file alright_mate_backup_last_attempt_timestamp_seconds "$attempted_at"
  if [ "$rclone_ready" -eq 1 ] && [ -n "$remote_stage" ]; then
    rclone purge "$remote_stage" >/dev/null 2>&1 || true
  fi
  case "$workdir" in
    /work/backup.*) rm -rf -- "$workdir" ;;
  esac
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_id="alright-mate_${timestamp}_${RELEASE_SHA}"
object_stage=".staging/${backup_id}"
object_final="backups/${backup_id}"
remote_stage="ops:${S3_BUCKET}/${object_stage}"
remote_final="ops:${S3_BUCKET}/${object_final}"
dump_ciphertext="${workdir}/database.dump.age"
metadata_ciphertext="${workdir}/metadata.json.age"

export RCLONE_CONFIG="/dev/null"
export RCLONE_CONFIG_OPS_TYPE="s3"
export RCLONE_CONFIG_OPS_PROVIDER="Other"
export RCLONE_CONFIG_OPS_ACCESS_KEY_ID="$S3_ACCESS_KEY"
export RCLONE_CONFIG_OPS_SECRET_ACCESS_KEY="$S3_SECRET_KEY"
export RCLONE_CONFIG_OPS_ENDPOINT="$S3_ENDPOINT"
export RCLONE_CONFIG_OPS_REGION="$S3_REGION"
export RCLONE_CONFIG_OPS_FORCE_PATH_STYLE="true"
rclone_ready=1

migration_version=$(psql "$DATABASE_URL" --no-psqlrc --tuples-only --no-align --command \
  "SELECT COALESCE(MAX(version_id), 0) FROM goose_db_version WHERE is_applied" | tr -d '[:space:]')
case "$migration_version" in
  *[!0-9]*|'') echo "backup failed: schema migration version is unavailable" >&2; exit 65 ;;
esac
migration_count=$(psql "$DATABASE_URL" --no-psqlrc --tuples-only --no-align --command \
  "SELECT COUNT(*) FROM goose_db_version WHERE is_applied AND version_id > 0" | tr -d '[:space:]')
expression_count=$(psql "$DATABASE_URL" --no-psqlrc --tuples-only --no-align --command \
  "SELECT COUNT(*) FROM expressions" | tr -d '[:space:]')
review_event_count=$(psql "$DATABASE_URL" --no-psqlrc --tuples-only --no-align --command \
  "SELECT COUNT(*) FROM review_events" | tr -d '[:space:]')
for source_count in "$migration_version" "$migration_count" "$expression_count" "$review_event_count"; do
  case "$source_count" in
    *[!0-9]*|'') echo "backup failed: source verification count is unavailable" >&2; exit 65 ;;
  esac
  if [ "$source_count" -lt 1 ]; then
    echo "backup failed: source verification counts must be non-zero" >&2
    exit 65
  fi
done

pg_dump \
  --dbname="$DATABASE_URL" \
  --format=custom \
  --no-owner \
  --no-privileges \
  | \
  age --encrypt --recipient "$AGE_RECIPIENT" --output "$dump_ciphertext"

ciphertext_sha256=$(sha256sum "$dump_ciphertext" | awk '{print $1}')
printf '{"backup_id":"%s","created_at":"%s","release_sha":"%s","migration_version":%s,"migration_count":%s,"expression_count":%s,"review_event_count":%s,"retention_days":%s,"ciphertext_sha256":"%s","format":"postgresql-18-custom+age"}\n' \
  "$backup_id" "$timestamp" "$RELEASE_SHA" "$migration_version" "$migration_count" \
  "$expression_count" "$review_event_count" "$RETENTION_DAYS" "$ciphertext_sha256" \
  | age --encrypt --recipient "$AGE_RECIPIENT" --output "$metadata_ciphertext"

rclone copyto "$dump_ciphertext" "${remote_stage}/database.dump.age"
rclone copyto "$metadata_ciphertext" "${remote_stage}/metadata.json.age"
rclone copyto "${remote_stage}/database.dump.age" "${remote_final}/database.dump.age" --log-level INFO --stats 0
rclone copyto "${remote_stage}/metadata.json.age" "${remote_final}/metadata.json.age" --log-level INFO --stats 0
printf '{"backup_id":"%s","ciphertext_sha256":"%s"}\n' "$backup_id" "$ciphertext_sha256" \
  | rclone rcat "${remote_final}/_COMPLETE"

success=1
duration_seconds=$(( $(date +%s) - started_at ))
echo "backup_result=success backup_id=${backup_id} duration_seconds=${duration_seconds}"
