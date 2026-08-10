#!/bin/sh
# shellcheck shell=busybox
set -eu
set -o pipefail

umask 077

backup_id="${1:?usage: restore-drill.sh BACKUP_ID}"
: "${AGE_IDENTITY_FILE:?AGE_IDENTITY_FILE is required}"
: "${RESTORE_NETWORK:?RESTORE_NETWORK is required}"
: "${S3_ACCESS_KEY:?S3_ACCESS_KEY is required}"
: "${S3_BUCKET:?S3_BUCKET is required}"
: "${S3_ENDPOINT:?S3_ENDPOINT is required}"
: "${S3_REGION:?S3_REGION is required}"
: "${S3_SECRET_KEY:?S3_SECRET_KEY is required}"

case "$backup_id" in
  *[!0-9A-Za-z._-]*|'') echo "restore configuration error: invalid backup ID" >&2; exit 64 ;;
esac
if [ "${#backup_id}" -gt 160 ]; then
  echo "restore configuration error: backup ID is too long" >&2
  exit 64
fi
case "$S3_ENDPOINT" in
  https://*|http://garage:3900) ;;
  *) echo "restore configuration error: S3_ENDPOINT must use HTTPS (except the private local Garage harness)" >&2; exit 64 ;;
esac
case "$S3_REGION" in
  *[!0-9A-Za-z._-]*|'') echo "restore configuration error: S3_REGION is invalid" >&2; exit 64 ;;
esac
if [ ! -f "$AGE_IDENTITY_FILE" ]; then
  echo "restore configuration error: age identity file is unavailable" >&2
  exit 66
fi
identity_mode=$(stat -c '%a' "$AGE_IDENTITY_FILE")
case "$identity_mode" in
  400|600) ;;
  *) echo "restore configuration error: age identity file must be owner-readable only" >&2; exit 66 ;;
esac
case "${RESTORE_DRILL_TEST_INJECT_CLEANUP_FAILURE:-none}" in
  none|container|workdir) ;;
  *) echo "restore configuration error: invalid cleanup failure injection" >&2; exit 64 ;;
esac

workdir=$(mktemp -d /work/restore.XXXXXX)
container_name="alright-mate-restore-$(printf '%s' "$backup_id" | cut -c1-32)-$$"
started_at=$(date +%s)
verification_complete=0

cleanup() {
  main_status=$?
  trap - EXIT INT TERM
  set +e
  cleanup_container_absent=0
  cleanup_workdir_absent=0

  if [ "${RESTORE_DRILL_TEST_INJECT_CLEANUP_FAILURE:-none}" != container ]; then
    docker rm -f "$container_name" >/dev/null 2>&1
  fi
  if ! docker inspect "$container_name" >/dev/null 2>&1; then
    cleanup_container_absent=1
  fi

  if [ "${RESTORE_DRILL_TEST_INJECT_CLEANUP_FAILURE:-none}" != workdir ]; then
    case "$workdir" in
      /work/restore.*) rm -rf -- "$workdir" ;;
    esac
  fi
  if test ! -e "$workdir"; then
    cleanup_workdir_absent=1
  fi

  final_status=$main_status
  final_result=failure
  if [ "$main_status" -eq 0 ] \
    && [ "$verification_complete" -eq 1 ] \
    && [ "$cleanup_container_absent" -eq 1 ] \
    && [ "$cleanup_workdir_absent" -eq 1 ]; then
    final_result=success
    final_status=0
  elif [ "$final_status" -eq 0 ]; then
    final_status=70
  fi
  duration_seconds=$(( $(date +%s) - started_at ))
  echo "restore_drill_result=${final_result} backup_id=${backup_id} duration_seconds=${duration_seconds} cleanup_container_absent=${cleanup_container_absent} cleanup_workdir_absent=${cleanup_workdir_absent}"
  exit "$final_status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

export RCLONE_CONFIG="/dev/null"
export RCLONE_CONFIG_OPS_TYPE="s3"
export RCLONE_CONFIG_OPS_PROVIDER="Other"
export RCLONE_CONFIG_OPS_ACCESS_KEY_ID="$S3_ACCESS_KEY"
export RCLONE_CONFIG_OPS_SECRET_ACCESS_KEY="$S3_SECRET_KEY"
export RCLONE_CONFIG_OPS_ENDPOINT="$S3_ENDPOINT"
export RCLONE_CONFIG_OPS_REGION="$S3_REGION"
export RCLONE_CONFIG_OPS_FORCE_PATH_STYLE="true"

remote_final="ops:${S3_BUCKET}/backups/${backup_id}"
rclone copyto "${remote_final}/_COMPLETE" "${workdir}/_COMPLETE"
rclone copyto "${remote_final}/metadata.json.age" "${workdir}/metadata.json.age"
rclone copyto "${remote_final}/database.dump.age" "${workdir}/database.dump.age"

jq -e --arg id "$backup_id" '.backup_id == $id' "${workdir}/_COMPLETE" >/dev/null
age --decrypt --identity "$AGE_IDENTITY_FILE" --output "${workdir}/metadata.json" "${workdir}/metadata.json.age"
expected_sha256=$(jq -er '.ciphertext_sha256 | select(test("^[0-9a-f]{64}$"))' "${workdir}/metadata.json")
expected_migration_version=$(jq -er '.migration_version | select(type == "number" and . > 0 and . == floor)' "${workdir}/metadata.json")
expected_migration_count=$(jq -er '.migration_count | select(type == "number" and . > 0 and . == floor)' "${workdir}/metadata.json")
expected_expression_count=$(jq -er '.expression_count | select(type == "number" and . > 0 and . == floor)' "${workdir}/metadata.json")
expected_review_event_count=$(jq -er '.review_event_count | select(type == "number" and . > 0 and . == floor)' "${workdir}/metadata.json")
actual_sha256=$(sha256sum "${workdir}/database.dump.age" | awk '{print $1}')
if [ "$expected_sha256" != "$actual_sha256" ]; then
  echo "restore verification failed: encrypted dump checksum mismatch" >&2
  exit 65
fi
jq -e --arg id "$backup_id" '.backup_id == $id and .format == "postgresql-18-custom+age"' \
  "${workdir}/metadata.json" >/dev/null

age --decrypt --identity "$AGE_IDENTITY_FILE" --output "${workdir}/database.dump" "${workdir}/database.dump.age"
chmod 0600 "${workdir}/database.dump"
pg_restore --list "${workdir}/database.dump" >/dev/null

restore_password=$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')
docker run --detach --rm \
  --name "$container_name" \
  --network "$RESTORE_NETWORK" \
  --read-only \
  --security-opt no-new-privileges:true \
  --cap-drop ALL \
  --cap-add CHOWN \
  --cap-add DAC_OVERRIDE \
  --cap-add FOWNER \
  --cap-add SETGID \
  --cap-add SETUID \
  --tmpfs /var/lib/postgresql/data:rw,noexec,nosuid,size=1024m,uid=70,gid=70 \
  --tmpfs /var/run/postgresql:rw,noexec,nosuid,size=16m,uid=70,gid=70 \
  --tmpfs /tmp:rw,noexec,nosuid,size=32m,uid=70,gid=70 \
  --env PGDATA=/var/lib/postgresql/data/pgdata \
  --env POSTGRES_DB=restore_drill \
  --env POSTGRES_PASSWORD="$restore_password" \
  cgr.dev/chainguard/postgres:latest@sha256:e67e686bf76586ff0b87fd42553f2dce5e461cb76e090f502ab129aaef141f12 \
  >/dev/null

restore_url="postgres://postgres:${restore_password}@${container_name}:5432/restore_drill?sslmode=disable"
ready=0
for _attempt in $(seq 1 60); do
  if pg_isready --dbname="$restore_url" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
if [ "$ready" -ne 1 ]; then
  echo "restore failed: disposable PostgreSQL did not become ready" >&2
  exit 69
fi

pg_restore --dbname="$restore_url" --exit-on-error --no-owner --no-privileges "${workdir}/database.dump"
psql "$restore_url" --no-psqlrc --set ON_ERROR_STOP=1 --tuples-only --no-align --command \
  "SELECT to_regclass('public.expressions') IS NOT NULL AND to_regclass('public.review_events') IS NOT NULL" \
  | grep -qx t
migration_version=$(psql "$restore_url" --no-psqlrc --tuples-only --no-align --command \
  "SELECT COALESCE(MAX(version_id), 0) FROM goose_db_version WHERE is_applied" | tr -d '[:space:]')
migration_count=$(psql "$restore_url" --no-psqlrc --tuples-only --no-align --command \
  "SELECT COUNT(*) FROM goose_db_version WHERE is_applied AND version_id > 0" | tr -d '[:space:]')
expression_count=$(psql "$restore_url" --no-psqlrc --tuples-only --no-align --command \
  "SELECT COUNT(*) FROM expressions" | tr -d '[:space:]')
review_event_count=$(psql "$restore_url" --no-psqlrc --tuples-only --no-align --command \
  "SELECT COUNT(*) FROM review_events" | tr -d '[:space:]')

if [ "$migration_version" != "$expected_migration_version" ]; then
  echo "restore verification failed: migration version does not match encrypted metadata" >&2
  exit 65
fi
if [ "$migration_count" != "$expected_migration_count" ]; then
  echo "restore verification failed: migration count does not match encrypted metadata" >&2
  exit 65
fi
if [ "$expression_count" != "$expected_expression_count" ]; then
  echo "restore verification failed: expression count does not match encrypted metadata" >&2
  exit 65
fi
if [ "$review_event_count" != "$expected_review_event_count" ]; then
  echo "restore verification failed: review-event count does not match encrypted metadata" >&2
  exit 65
fi

verification_complete=1
echo "restore_smoke=verified migration_version=${migration_version} migrations=${migration_count} expressions=${expression_count} review_events=${review_event_count} ciphertext_sha256=${actual_sha256}"
