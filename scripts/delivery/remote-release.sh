#!/usr/bin/env bash
set -euo pipefail

umask 077

state_root=${DELIVERY_STATE_ROOT:-/opt/alright-mate/delivery}
install_root=${DELIVERY_INSTALL_ROOT:-/opt/alright-mate}
systemd_runtime_root=${DELIVERY_SYSTEMD_RUNTIME_ROOT:-/run/systemd/system}
backup_unit=alright-mate-backup.service
minimum_available_kb=${DELIVERY_MINIMUM_AVAILABLE_KB:-1048576}
backup_release_environment_file=${DELIVERY_BACKUP_RELEASE_ENV_FILE:-/run/alright-mate-backup-release.env}
durable_backup_release_environment_file=${DELIVERY_DURABLE_BACKUP_RELEASE_ENV_FILE:-/etc/alright-mate/backup-release.env}
state_file="${state_root}/release.env"
pending_file="${state_root}/pending.env"
lock_file="${state_root}/lock"
history_file="${state_root}/history.log"
operations_root="${state_root}/operations"
backup_release_dropin="${systemd_runtime_root}/${backup_unit}.d/release-environment.conf"
backup_runtime_cleanup_active=0

cleanup_backup_runtime_files() {
  local cleanup_status=0
  rm -f -- "$backup_release_dropin" "$backup_release_environment_file" || cleanup_status=1
  systemctl daemon-reload >/dev/null 2>&1 || cleanup_status=1
  if (( cleanup_status == 0 )); then
    backup_runtime_cleanup_active=0
  fi
  return "$cleanup_status"
}

cleanup_remote_exit() {
  local status=$?
  trap - EXIT INT TERM
  if (( backup_runtime_cleanup_active == 1 )) && ! cleanup_backup_runtime_files; then
    status=1
  fi
  exit "$status"
}
trap cleanup_remote_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

validate_sha() {
  [[ ${1:-} =~ ^[0-9a-f]{40}$ ]]
}

validate_operation_id() {
  [[ ${1:-} =~ ^[0-9a-f]{32}$ ]]
}

validate_image_reference() {
  [[ ${1:-} =~ ^ghcr\.io/[a-z0-9]+([._-][a-z0-9]+)*(/[a-z0-9]+([._-][a-z0-9]+)*)+@sha256:[0-9a-f]{64}$ ]]
}

validate_boolean() {
  [[ ${1:-} == "true" || ${1:-} == "false" ]]
}

validate_unsafe_audit() {
  local allow_unsafe=${1:-}
  local reason=${2:-}
  validate_boolean "$allow_unsafe" || { echo "unsafe migration override is invalid" >&2; return 64; }
  if [[ "$allow_unsafe" == "true" ]]; then
    if (( ${#reason} < 12 || ${#reason} > 240 )) || [[ "$reason" == *$'\n'* || "$reason" == *$'\r'* ]]; then
      echo "unsafe migration override requires a 12-240 character single-line reason" >&2
      return 64
    fi
  elif [[ -n "$reason" ]]; then
    echo "unsafe migration reason requires the protected override" >&2
    return 64
  fi
}

state_value() {
  local key=$1
  local file=$2
  awk -F= -v wanted="$key" '$1 == wanted { print substr($0, index($0, "=") + 1); exit }' "$file"
}

operation_file() {
  printf '%s/%s.env\n' "$operations_root" "$1"
}

validate_operation_file() {
  local file=$1
  [[ -f "$file" ]] || { echo "delivery operation is unknown" >&2; return 65; }
  if [[ $(grep -c '^operation_schema=' "$file") != 1 ]] ||
      [[ $(state_value operation_schema "$file") != "2" ]]; then
    echo "delivery operation schema is unsupported" >&2
    return 65
  fi
  if [[ $(grep -c '^preflight_complete=' "$file") != 1 ]] ||
      ! validate_boolean "$(state_value preflight_complete "$file")"; then
    echo "delivery operation preflight state is invalid" >&2
    return 65
  fi
}

release_value() {
  local slot=$1
  local field=$2
  local key
  case "$field" in
    sha) key=$slot ;;
    api|migration|web|backup|rollback_safe) key="${slot}_${field}" ;;
    *) echo "unsupported release state field" >&2; return 64 ;;
  esac
  state_value "$key" "$state_file"
}

validate_release_slot() {
  local slot=$1
  local sha
  local api
  local migration
  local web
  local backup
  local rollback_safe
  [[ -f "$state_file" ]] || { echo "a complete release state is required" >&2; return 65; }
  sha=$(release_value "$slot" sha)
  api=$(release_value "$slot" api)
  migration=$(release_value "$slot" migration)
  web=$(release_value "$slot" web)
  backup=$(release_value "$slot" backup)
  rollback_safe=$(release_value "$slot" rollback_safe)
  validate_sha "$sha" || { echo "recorded ${slot} release SHA is invalid" >&2; return 65; }
  validate_image_reference "$api" || { echo "recorded ${slot} API digest is invalid" >&2; return 65; }
  validate_image_reference "$migration" || { echo "recorded ${slot} migration digest is invalid" >&2; return 65; }
  validate_image_reference "$web" || { echo "recorded ${slot} web digest is invalid" >&2; return 65; }
  validate_image_reference "$backup" || { echo "recorded ${slot} backup digest is invalid" >&2; return 65; }
  validate_boolean "$rollback_safe" || { echo "recorded ${slot} rollback safety is invalid" >&2; return 65; }
}

check_live_release() {
  local service
  local container_id
  local state

  docker info >/dev/null
  for service in db api web gateway; do
    container_id=$(docker ps --quiet \
      --filter label=com.docker.compose.project=alright-mate \
      --filter "label=com.docker.compose.service=${service}")
    if [[ -z "$container_id" ]]; then
      echo "current ${service} container is not running" >&2
      return 1
    fi
    state=$(docker inspect --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' "$container_id")
    if [[ "$state" != "running healthy" ]]; then
      echo "current ${service} container is not healthy" >&2
      return 1
    fi
  done
}

check_runtime_absent() {
  local container_ids

  docker info >/dev/null
  if [[ -e "${install_root}/current" || -L "${install_root}/current" ]]; then
    echo "an unrecorded current release link prevents initial deployment" >&2
    return 1
  fi
  if [[ -e "$durable_backup_release_environment_file" ]]; then
    echo "an unrecorded durable backup identity prevents initial deployment" >&2
    return 1
  fi
  container_ids=$(docker ps --all --quiet \
    --filter label=com.docker.compose.project=alright-mate)
  if [[ -n "$container_ids" ]]; then
    echo "unrecorded Alright, Mate? containers prevent initial deployment" >&2
    return 1
  fi
}

compose_container_id() {
  local service=$1
  local include_stopped=${2:-false}
  local ids
  local -a docker_arguments=(ps --quiet)
  if [[ "$include_stopped" == "true" ]]; then
    docker_arguments+=(--all)
  fi
  ids=$(docker "${docker_arguments[@]}" \
    --filter label=com.docker.compose.project=alright-mate \
    --filter "label=com.docker.compose.service=${service}")
  if [[ -z "$ids" || "$ids" == *$'\n'* ]]; then
    echo "expected exactly one ${service} container" >&2
    return 1
  fi
  printf '%s\n' "$ids"
}

verify_container_image() {
  local service=$1
  local expected_image=$2
  local include_stopped=${3:-false}
  local container_id
  local actual_image
  container_id=$(compose_container_id "$service" "$include_stopped")
  actual_image=$(docker inspect --format '{{.Config.Image}}' "$container_id")
  if [[ "$actual_image" != "$expected_image" ]]; then
    echo "running ${service} image does not match the release digest" >&2
    return 1
  fi
  printf '%s\n' "$container_id"
}

verify_runtime_release() {
  local release_sha=$1
  local api_image=$2
  local migration_image=$3
  local web_image=$4
  local backup_image=$5
  local expected_release_directory
  local current_release_directory
  local api_container
  local web_container
  local migration_container
  local container_state

  validate_sha "$release_sha" || { echo "runtime release SHA is invalid" >&2; return 64; }
  validate_image_reference "$api_image" || { echo "runtime API digest is invalid" >&2; return 64; }
  validate_image_reference "$migration_image" || { echo "runtime migration digest is invalid" >&2; return 64; }
  validate_image_reference "$web_image" || { echo "runtime web digest is invalid" >&2; return 64; }
  validate_image_reference "$backup_image" || { echo "runtime backup digest is invalid" >&2; return 64; }

  expected_release_directory=$(readlink -f "${install_root}/releases/${release_sha}")
  current_release_directory=$(readlink -f "${install_root}/current")
  if [[ -z "$expected_release_directory" || "$current_release_directory" != "$expected_release_directory" ]]; then
    echo "current release link does not match the requested release SHA" >&2
    return 1
  fi
  if [[ ! -f "$durable_backup_release_environment_file" ]] ||
      [[ $(state_value RELEASE_SHA "$durable_backup_release_environment_file") != "$release_sha" ]] ||
      [[ $(state_value BACKUP_TOOLS_IMAGE "$durable_backup_release_environment_file") != "$backup_image" ]] ||
      [[ $(grep -c '^RELEASE_SHA=' "$durable_backup_release_environment_file") != 1 ]] ||
      [[ $(grep -c '^BACKUP_TOOLS_IMAGE=' "$durable_backup_release_environment_file") != 1 ]]; then
    echo "durable backup release identity does not match the requested release" >&2
    return 1
  fi

  api_container=$(verify_container_image api "$api_image")
  web_container=$(verify_container_image web "$web_image")
  migration_container=$(verify_container_image migrate "$migration_image" true)
  for container in "$api_container" "$web_container"; do
    container_state=$(docker inspect --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' "$container")
    [[ "$container_state" == "running healthy" ]] || {
      echo "release application container is not running and healthy" >&2
      return 1
    }
  done
  container_state=$(docker inspect --format '{{.State.Status}} {{.State.ExitCode}}' "$migration_container")
  [[ "$container_state" == "exited 0" ]] || {
    echo "release migration container did not complete successfully" >&2
    return 1
  }
  docker image inspect "$backup_image" >/dev/null
  printf 'runtime_identity=verified release_sha=%s\n' "$release_sha"
}

check_disk() {
  local available_kb
  available_kb=$(df -Pk "$install_root" | awk 'NR == 2 { print $4 }')
  if [[ ! "$available_kb" =~ ^[0-9]+$ ]] || (( available_kb < minimum_available_kb )); then
    echo "production host has insufficient free disk space" >&2
    return 1
  fi
}

run_backup() {
  local release_sha=$1
  local backup_image=$2
  local dropin_directory="${systemd_runtime_root}/${backup_unit}.d"
  local temporary_environment
  local temporary_dropin
  local result
  local exit_status
  local backup_status=1
  local cleanup_status=0

  validate_sha "$release_sha" || { echo "backup release SHA is invalid" >&2; return 64; }
  validate_image_reference "$backup_image" || { echo "backup tools digest is invalid" >&2; return 64; }
  if [[ ! -d "$dropin_directory" ]] && ! mkdir -p "$dropin_directory"; then
    echo "backup runtime drop-in directory could not be created" >&2
    return 1
  fi
  if ! temporary_environment=$(mktemp "${backup_release_environment_file}.XXXXXX"); then
    echo "backup runtime environment could not be staged" >&2
    return 1
  fi
  if ! temporary_dropin=$(mktemp "${dropin_directory}/.release-environment.XXXXXX"); then
    rm -f -- "$temporary_environment"
    echo "backup runtime drop-in could not be staged" >&2
    return 1
  fi
  if ! {
    printf 'RELEASE_SHA=%s\nBACKUP_TOOLS_IMAGE=%s\n' \
      "$release_sha" "$backup_image" >"$temporary_environment"
    chmod 0600 "$temporary_environment" "$temporary_dropin"
    printf '[Service]\nEnvironmentFile=%s\n' \
      "$backup_release_environment_file" >"$temporary_dropin"
    mv -f "$temporary_environment" "$backup_release_environment_file"
    backup_runtime_cleanup_active=1
    mv -f "$temporary_dropin" "$backup_release_dropin"
  }; then
    rm -f -- "$temporary_environment" "$temporary_dropin"
    cleanup_backup_runtime_files || true
    return 1
  fi
  if ! systemctl daemon-reload; then
    cleanup_backup_runtime_files || true
    return 1
  fi

  if systemctl start "$backup_unit"; then
    result=$(systemctl show "$backup_unit" --property=Result --value)
    exit_status=$(systemctl show "$backup_unit" --property=ExecMainStatus --value)
    if [[ "$result" == "success" && "$exit_status" == "0" ]]; then
      backup_status=0
    fi
  else
    result=failed
    exit_status=1
  fi
  cleanup_backup_runtime_files || cleanup_status=1
  if (( backup_status != 0 )); then
    echo "encrypted pre-deployment backup failed" >&2
    return 1
  fi
  if (( cleanup_status != 0 )); then
    echo "backup runtime identity cleanup failed" >&2
    return 1
  fi
}

print_operation() {
  local operation_id=$1
  local file
  file=$(operation_file "$operation_id")
  validate_operation_file "$file"
  cat "$file"
}

write_operation() {
  local operation_id=$1
  local mode=$2
  local target_sha=$3
  local target_api=$4
  local target_migration=$5
  local target_web=$6
  local target_backup=$7
  local target_rollback_safe=$8
  local previous_sha=$9
  local previous_api=${10}
  local previous_migration=${11}
  local previous_web=${12}
  local previous_backup=${13}
  local previous_rollback_safe=${14}
  local initial_deploy=${15}
  local unsafe_override=${16}
  local unsafe_reason=${17}
  local preflight_complete=${18}
  local file
  local temporary
  local timestamp

  file=$(operation_file "$operation_id")
  temporary=$(mktemp "${operations_root}/.${operation_id}.XXXXXX")
  timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  printf '%s\n' \
    "operation_schema=2" \
    "operation_id=${operation_id}" \
    "operation_status=preparing" \
    "mode=${mode}" \
    "preflight_complete=${preflight_complete}" \
    "initial_deploy=${initial_deploy}" \
    "target_sha=${target_sha}" \
    "target_api_image_reference=${target_api}" \
    "target_migration_image_reference=${target_migration}" \
    "target_web_image_reference=${target_web}" \
    "target_backup_image_reference=${target_backup}" \
    "target_rollback_safe=${target_rollback_safe}" \
    "previous_sha=${previous_sha}" \
    "previous_api_image_reference=${previous_api}" \
    "previous_migration_image_reference=${previous_migration}" \
    "previous_web_image_reference=${previous_web}" \
    "previous_backup_image_reference=${previous_backup}" \
    "previous_rollback_safe=${previous_rollback_safe}" \
    "unsafe_migration_override=${unsafe_override}" \
    "unsafe_migration_reason=${unsafe_reason}" \
    "outcome=" \
    "created_at=${timestamp}" >"$temporary"
  chmod 0600 "$temporary"
  mv -f "$temporary" "$file"
  cp "$file" "${pending_file}.tmp"
  chmod 0600 "${pending_file}.tmp"
  mv -f "${pending_file}.tmp" "$pending_file"
}

set_operation_preflight() {
  local operation_id=$1
  local initial_deploy=$2
  local target_sha=$3
  local target_api=$4
  local target_migration=$5
  local target_web=$6
  local target_backup=$7
  local target_rollback_safe=$8
  local previous_sha=$9
  local previous_api=${10}
  local previous_migration=${11}
  local previous_web=${12}
  local previous_backup=${13}
  local previous_rollback_safe=${14}
  local file
  local temporary

  file=$(operation_file "$operation_id")
  validate_operation_file "$file"
  temporary=$(mktemp "${operations_root}/.${operation_id}.XXXXXX")
  awk -F= \
    -v initial_deploy="$initial_deploy" \
    -v target_sha="$target_sha" \
    -v target_api="$target_api" \
    -v target_migration="$target_migration" \
    -v target_web="$target_web" \
    -v target_backup="$target_backup" \
    -v target_rollback_safe="$target_rollback_safe" \
    -v previous_sha="$previous_sha" \
    -v previous_api="$previous_api" \
    -v previous_migration="$previous_migration" \
    -v previous_web="$previous_web" \
    -v previous_backup="$previous_backup" \
    -v previous_rollback_safe="$previous_rollback_safe" '
      $1 == "preflight_complete" { print "preflight_complete=true"; next }
      $1 == "initial_deploy" { print "initial_deploy=" initial_deploy; next }
      $1 == "target_sha" { print "target_sha=" target_sha; next }
      $1 == "target_api_image_reference" { print "target_api_image_reference=" target_api; next }
      $1 == "target_migration_image_reference" { print "target_migration_image_reference=" target_migration; next }
      $1 == "target_web_image_reference" { print "target_web_image_reference=" target_web; next }
      $1 == "target_backup_image_reference" { print "target_backup_image_reference=" target_backup; next }
      $1 == "target_rollback_safe" { print "target_rollback_safe=" target_rollback_safe; next }
      $1 == "previous_sha" { print "previous_sha=" previous_sha; next }
      $1 == "previous_api_image_reference" { print "previous_api_image_reference=" previous_api; next }
      $1 == "previous_migration_image_reference" { print "previous_migration_image_reference=" previous_migration; next }
      $1 == "previous_web_image_reference" { print "previous_web_image_reference=" previous_web; next }
      $1 == "previous_backup_image_reference" { print "previous_backup_image_reference=" previous_backup; next }
      $1 == "previous_rollback_safe" { print "previous_rollback_safe=" previous_rollback_safe; next }
      { print }
    ' "$file" >"$temporary"
  chmod 0600 "$temporary"
  mv -f "$temporary" "$file"
  cp "$file" "${pending_file}.tmp"
  chmod 0600 "${pending_file}.tmp"
  mv -f "${pending_file}.tmp" "$pending_file"
}

set_operation_status() {
  local operation_id=$1
  local status=$2
  local outcome=${3:-}
  local file
  local temporary
  file=$(operation_file "$operation_id")
  validate_operation_file "$file"
  temporary=$(mktemp "${operations_root}/.${operation_id}.XXXXXX")
  awk -F= -v status="$status" -v outcome="$outcome" '
    $1 == "operation_status" { print "operation_status=" status; next }
    $1 == "outcome" { print "outcome=" outcome; next }
    { print }
  ' "$file" >"$temporary"
  chmod 0600 "$temporary"
  mv -f "$temporary" "$file"
  if [[ -f "$lock_file" && $(lock_owner) == "$operation_id" ]]; then
    cp "$file" "${pending_file}.tmp"
    chmod 0600 "${pending_file}.tmp"
    mv -f "${pending_file}.tmp" "$pending_file"
  fi
}

lock_owner() {
  [[ -f "$lock_file" ]] && tr -d '\r\n' <"$lock_file"
}

release_operation_lock() {
  local operation_id=$1
  if [[ -f "$lock_file" && $(lock_owner) == "$operation_id" ]]; then
    rm -f -- "$pending_file"
    rm -f -- "$lock_file"
  fi
}

record_abort() {
  local operation_id=$1
  local outcome=$2
  local target
  local timestamp
  local unsafe_override
  local unsafe_reason
  local operation
  operation=$(operation_file "$operation_id")
  validate_operation_file "$operation"
  target=$(state_value target_sha "$operation")
  unsafe_override=$(state_value unsafe_migration_override "$operation")
  unsafe_reason=$(state_value unsafe_migration_reason "$operation")
  timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  set_operation_status "$operation_id" aborted "$outcome"
  printf '%s operation=%s result=%s target=%s database_rollback=none unsafe_migration_override=%s unsafe_migration_reason=%q\n' \
    "$timestamp" "$operation_id" "$outcome" "$target" "$unsafe_override" "$unsafe_reason" >>"$history_file"
  chmod 0600 "$history_file"
  release_operation_lock "$operation_id"
}

operation_matches_deploy() {
  local file=$1
  local target_sha=$2
  local target_api=$3
  local target_migration=$4
  local target_web=$5
  local target_backup=$6
  local target_rollback_safe=$7
  local unsafe_override=$8
  local unsafe_reason=$9
  [[ $(state_value mode "$file") == "begin-deploy" && \
    $(state_value target_sha "$file") == "$target_sha" && \
    $(state_value target_api_image_reference "$file") == "$target_api" && \
    $(state_value target_migration_image_reference "$file") == "$target_migration" && \
    $(state_value target_web_image_reference "$file") == "$target_web" && \
    $(state_value target_backup_image_reference "$file") == "$target_backup" && \
    $(state_value target_rollback_safe "$file") == "$target_rollback_safe" && \
    $(state_value unsafe_migration_override "$file") == "$unsafe_override" && \
    $(state_value unsafe_migration_reason "$file") == "$unsafe_reason" ]]
}

operation_matches_rollback() {
  local file=$1
  local unsafe_override=$2
  local unsafe_reason=$3
  [[ $(state_value mode "$file") == "begin-rollback" && \
    $(state_value unsafe_migration_override "$file") == "$unsafe_override" && \
    $(state_value unsafe_migration_reason "$file") == "$unsafe_reason" ]]
}

complete_operation_preflight() {
  local operation_id=$1
  local operation
  local preflight_complete
  local mode
  local allow_unsafe
  local unsafe_reason
  local target_sha=
  local target_api=
  local target_migration=
  local target_web=
  local target_backup=
  local target_rollback_safe=
  local previous_sha=
  local previous_api=
  local previous_migration=
  local previous_web=
  local previous_backup=
  local previous_rollback_safe=
  local initial_deploy=false

  operation=$(operation_file "$operation_id")
  validate_operation_file "$operation"
  preflight_complete=$(state_value preflight_complete "$operation")
  validate_boolean "$preflight_complete" || {
    echo "delivery operation preflight state is invalid" >&2
    return 65
  }
  [[ "$preflight_complete" == "false" ]] || return 0
  [[ $(state_value operation_status "$operation") == "preparing" ]] || {
    echo "incomplete preflight is not preparing" >&2
    return 65
  }
  [[ -f "$lock_file" && $(lock_owner) == "$operation_id" ]] || {
    echo "preparing delivery operation no longer owns the release lock" >&2
    return 65
  }

  mode=$(state_value mode "$operation")
  allow_unsafe=$(state_value unsafe_migration_override "$operation")
  unsafe_reason=$(state_value unsafe_migration_reason "$operation")
  validate_unsafe_audit "$allow_unsafe" "$unsafe_reason"
  check_disk

  case "$mode" in
    begin-deploy)
      target_sha=$(state_value target_sha "$operation")
      target_api=$(state_value target_api_image_reference "$operation")
      target_migration=$(state_value target_migration_image_reference "$operation")
      target_web=$(state_value target_web_image_reference "$operation")
      target_backup=$(state_value target_backup_image_reference "$operation")
      target_rollback_safe=$(state_value target_rollback_safe "$operation")
      validate_sha "$target_sha" || { echo "target release SHA is invalid" >&2; return 64; }
      validate_image_reference "$target_api" || { echo "target API digest is invalid" >&2; return 64; }
      validate_image_reference "$target_migration" || { echo "target migration digest is invalid" >&2; return 64; }
      validate_image_reference "$target_web" || { echo "target web digest is invalid" >&2; return 64; }
      validate_image_reference "$target_backup" || { echo "target backup digest is invalid" >&2; return 64; }
      validate_boolean "$target_rollback_safe" || { echo "target rollback safety is invalid" >&2; return 64; }
      if [[ "$target_rollback_safe" != "true" && "$allow_unsafe" != "true" ]]; then
        echo "target migration declaration does not permit automatic application rollback" >&2
        return 65
      fi
      if [[ -f "$state_file" ]]; then
        validate_release_slot current
        check_live_release
        previous_sha=$(release_value current sha)
        previous_api=$(release_value current api)
        previous_migration=$(release_value current migration)
        previous_web=$(release_value current web)
        previous_backup=$(release_value current backup)
        previous_rollback_safe=$(release_value current rollback_safe)
      else
        check_runtime_absent
        initial_deploy=true
        previous_sha=$target_sha
        previous_api=$target_api
        previous_migration=$target_migration
        previous_web=$target_web
        previous_backup=$target_backup
        previous_rollback_safe=$target_rollback_safe
      fi
      ;;
    begin-rollback)
      validate_release_slot current
      check_live_release
      previous_sha=$(release_value current sha)
      previous_api=$(release_value current api)
      previous_migration=$(release_value current migration)
      previous_web=$(release_value current web)
      previous_backup=$(release_value current backup)
      previous_rollback_safe=$(release_value current rollback_safe)
      if [[ "$previous_rollback_safe" != "true" && "$allow_unsafe" != "true" ]]; then
        echo "current migration declaration does not permit an application rollback" >&2
        return 65
      fi
      validate_release_slot previous
      target_sha=$(release_value previous sha)
      target_api=$(release_value previous api)
      target_migration=$(release_value previous migration)
      target_web=$(release_value previous web)
      target_backup=$(release_value previous backup)
      target_rollback_safe=$(release_value previous rollback_safe)
      ;;
    *)
      echo "delivery operation mode is invalid" >&2
      return 65
      ;;
  esac

  set_operation_preflight "$operation_id" "$initial_deploy" \
    "$target_sha" "$target_api" "$target_migration" "$target_web" "$target_backup" "$target_rollback_safe" \
    "$previous_sha" "$previous_api" "$previous_migration" "$previous_web" "$previous_backup" "$previous_rollback_safe"
}

complete_operation_preparation() {
  local operation_id=$1
  local operation
  local initial_deploy
  local status

  operation=$(operation_file "$operation_id")
  validate_operation_file "$operation"
  status=$(state_value operation_status "$operation")
  [[ "$status" == "preparing" ]] || return 0
  [[ -f "$lock_file" && $(lock_owner) == "$operation_id" ]] || {
    echo "preparing delivery operation no longer owns the release lock" >&2
    return 65
  }
  complete_operation_preflight "$operation_id"
  operation=$(operation_file "$operation_id")
  initial_deploy=$(state_value initial_deploy "$operation")
  validate_boolean "$initial_deploy" || {
    echo "initial deployment state is invalid" >&2
    return 65
  }
  if [[ "$initial_deploy" != "true" ]]; then
    run_backup \
      "$(state_value previous_sha "$operation")" \
      "$(state_value previous_backup_image_reference "$operation")"
  fi
  set_operation_status "$operation_id" pending
}

begin_release() {
  local mode=$1
  local operation_id=$2
  shift 2
  local requested_sha=${1:-}
  local requested_api=${2:-}
  local requested_migration=${3:-}
  local requested_web=${4:-}
  local requested_backup=${5:-}
  local requested_rollback_safe=${6:-}
  local requested_allow_unsafe=${7:-false}
  local requested_unsafe_reason=${8:-}
  local allow_unsafe=false
  local unsafe_reason=
  local operation
  local target_sha=
  local target_api=
  local target_migration=
  local target_web=
  local target_backup=
  local target_rollback_safe=
  local previous_sha=
  local previous_api=
  local previous_migration=
  local previous_web=
  local previous_backup=
  local previous_rollback_safe=
  local initial_deploy=false
  local keep_lock=0

  validate_operation_id "$operation_id" || { echo "operation ID must be 32 lower-case hexadecimal characters" >&2; return 64; }
  [[ -d "$state_root" ]] || mkdir -p "$state_root"
  [[ -d "$operations_root" ]] || mkdir -p "$operations_root"
  chmod 0700 "$state_root" "$operations_root"
  operation=$(operation_file "$operation_id")

  case "$mode" in
    begin-deploy)
      allow_unsafe=$requested_allow_unsafe
      unsafe_reason=$requested_unsafe_reason
      target_sha=$requested_sha
      target_api=$requested_api
      target_migration=$requested_migration
      target_web=$requested_web
      target_backup=$requested_backup
      target_rollback_safe=$requested_rollback_safe
      ;;
    begin-rollback)
      allow_unsafe=${1:-false}
      unsafe_reason=${2:-}
      ;;
    *) echo "unsupported begin mode" >&2; return 64 ;;
  esac
  validate_unsafe_audit "$allow_unsafe" "$unsafe_reason"
  if [[ "$mode" == "begin-deploy" ]]; then
    validate_sha "$target_sha" || { echo "target release SHA is invalid" >&2; return 64; }
    validate_image_reference "$target_api" || { echo "target API digest is invalid" >&2; return 64; }
    validate_image_reference "$target_migration" || { echo "target migration digest is invalid" >&2; return 64; }
    validate_image_reference "$target_web" || { echo "target web digest is invalid" >&2; return 64; }
    validate_image_reference "$target_backup" || { echo "target backup digest is invalid" >&2; return 64; }
    validate_boolean "$target_rollback_safe" || { echo "target rollback safety is invalid" >&2; return 64; }
    if [[ "$target_rollback_safe" != "true" && "$allow_unsafe" != "true" ]]; then
      echo "target migration declaration does not permit automatic application rollback" >&2
      return 65
    fi
  fi

  if [[ -f "$operation" ]]; then
    validate_operation_file "$operation"
    if [[ "$mode" == "begin-deploy" ]] && ! operation_matches_deploy \
        "$operation" "$requested_sha" "$requested_api" "$requested_migration" \
        "$requested_web" "$requested_backup" "$requested_rollback_safe" \
        "$allow_unsafe" "$unsafe_reason"; then
      echo "operation ID is already bound to another release request" >&2
      return 65
    fi
    if [[ "$mode" == "begin-rollback" ]] && ! operation_matches_rollback \
        "$operation" "$allow_unsafe" "$unsafe_reason"; then
      echo "operation ID is already bound to another release request" >&2
      return 65
    fi
    complete_operation_preparation "$operation_id"
    print_operation "$operation_id"
    return 0
  fi

  if ! (set -o noclobber; printf '%s\n' "$operation_id" >"$lock_file") 2>/dev/null; then
    echo "another delivery operation owns the production release lock" >&2
    return 73
  fi
  cleanup_failed_begin() {
    local status=$?
    if (( keep_lock == 0 )); then
      rm -f -- "$pending_file"
      if [[ -f "$lock_file" && $(lock_owner) == "$operation_id" ]]; then
        rm -f -- "$lock_file"
      fi
    fi
    return "$status"
  }
  trap cleanup_failed_begin RETURN

  write_operation "$operation_id" "$mode" \
    "$target_sha" "$target_api" "$target_migration" "$target_web" "$target_backup" "$target_rollback_safe" \
    "$previous_sha" "$previous_api" "$previous_migration" "$previous_web" "$previous_backup" "$previous_rollback_safe" \
    "$initial_deploy" "$allow_unsafe" "$unsafe_reason" false
  keep_lock=1
  complete_operation_preparation "$operation_id"
  print_operation "$operation_id"
}

state_matches_operation() {
  local operation=$1
  [[ -f "$state_file" && \
    $(release_value current sha) == $(state_value target_sha "$operation") && \
    $(release_value current api) == $(state_value target_api_image_reference "$operation") && \
    $(release_value current migration) == $(state_value target_migration_image_reference "$operation") && \
    $(release_value current web) == $(state_value target_web_image_reference "$operation") && \
    $(release_value current backup) == $(state_value target_backup_image_reference "$operation") && \
    $(release_value current rollback_safe) == $(state_value target_rollback_safe "$operation") && \
    $(release_value previous sha) == $(state_value previous_sha "$operation") && \
    $(release_value previous api) == $(state_value previous_api_image_reference "$operation") && \
    $(release_value previous migration) == $(state_value previous_migration_image_reference "$operation") && \
    $(release_value previous web) == $(state_value previous_web_image_reference "$operation") && \
    $(release_value previous backup) == $(state_value previous_backup_image_reference "$operation") && \
    $(release_value previous rollback_safe) == $(state_value previous_rollback_safe "$operation") ]]
}

finish_release() {
  local operation_id=${1:-}
  local operation
  local status
  local timestamp
  local temporary_state
  local unsafe_override
  local unsafe_reason

  validate_operation_id "$operation_id" || { echo "operation ID is invalid" >&2; return 64; }
  operation=$(operation_file "$operation_id")
  validate_operation_file "$operation"
  status=$(state_value operation_status "$operation")
  if [[ "$status" == "success" ]]; then
    release_operation_lock "$operation_id"
    print_operation "$operation_id"
    return 0
  fi
  [[ "$status" == "pending" ]] || { echo "aborted delivery operation cannot finish" >&2; return 65; }
  [[ -f "$lock_file" && $(lock_owner) == "$operation_id" ]] || {
    echo "delivery operation no longer owns the release lock" >&2
    return 65
  }
  verify_runtime_release \
    "$(state_value target_sha "$operation")" \
    "$(state_value target_api_image_reference "$operation")" \
    "$(state_value target_migration_image_reference "$operation")" \
    "$(state_value target_web_image_reference "$operation")" \
    "$(state_value target_backup_image_reference "$operation")" >/dev/null

  if ! state_matches_operation "$operation"; then
    timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    temporary_state=$(mktemp "${state_root}/.release.XXXXXX")
    printf '%s\n' \
      "current=$(state_value target_sha "$operation")" \
      "current_api=$(state_value target_api_image_reference "$operation")" \
      "current_migration=$(state_value target_migration_image_reference "$operation")" \
      "current_web=$(state_value target_web_image_reference "$operation")" \
      "current_backup=$(state_value target_backup_image_reference "$operation")" \
      "current_rollback_safe=$(state_value target_rollback_safe "$operation")" \
      "previous=$(state_value previous_sha "$operation")" \
      "previous_api=$(state_value previous_api_image_reference "$operation")" \
      "previous_migration=$(state_value previous_migration_image_reference "$operation")" \
      "previous_web=$(state_value previous_web_image_reference "$operation")" \
      "previous_backup=$(state_value previous_backup_image_reference "$operation")" \
      "previous_rollback_safe=$(state_value previous_rollback_safe "$operation")" \
      "updated_at=${timestamp}" >"$temporary_state"
    chmod 0600 "$temporary_state"
    mv -f "$temporary_state" "$state_file"
  fi

  set_operation_status "$operation_id" success completed
  unsafe_override=$(state_value unsafe_migration_override "$operation")
  unsafe_reason=$(state_value unsafe_migration_reason "$operation")
  printf '%s operation=%s result=success current=%s previous=%s unsafe_migration_override=%s unsafe_migration_reason=%q\n' \
    "${timestamp:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}" "$operation_id" \
    "$(state_value target_sha "$operation")" "$(state_value previous_sha "$operation")" \
    "$unsafe_override" "$unsafe_reason" >>"$history_file"
  chmod 0600 "$history_file"
  release_operation_lock "$operation_id"
  print_operation "$operation_id"
}

abort_release() {
  local operation_id=${1:-}
  local outcome=${2:-failed}
  local operation
  local status

  validate_operation_id "$operation_id" || { echo "operation ID is invalid" >&2; return 64; }
  [[ "$outcome" =~ ^[a-z-]+$ ]] || { echo "release outcome is invalid" >&2; return 64; }
  operation=$(operation_file "$operation_id")
  validate_operation_file "$operation"
  status=$(state_value operation_status "$operation")
  if [[ "$status" == "aborted" ]]; then
    release_operation_lock "$operation_id"
    print_operation "$operation_id"
    return 0
  fi
  [[ "$status" == "preparing" || "$status" == "pending" ]] || {
    echo "successful delivery operation cannot be aborted" >&2
    return 65
  }
  [[ -f "$lock_file" && $(lock_owner) == "$operation_id" ]] || {
    echo "delivery operation no longer owns the release lock" >&2
    return 65
  }
  record_abort "$operation_id" "$outcome"
  print_operation "$operation_id"
}

status_release() {
  local operation_id=${1:-}
  validate_operation_id "$operation_id" || { echo "operation ID is invalid" >&2; return 64; }
  print_operation "$operation_id"
}

command=${1:-}
case "$command" in
  begin-deploy)
    [[ $# -eq 10 ]] || { echo "begin-deploy requires operation, SHA, four digests, compatibility and unsafe audit" >&2; exit 64; }
    begin_release "$command" "$2" "$3" "$4" "$5" "$6" "$7" "$8" "$9" "${10}"
    ;;
  begin-rollback)
    [[ $# -eq 4 ]] || { echo "begin-rollback requires operation, unsafe-rollback flag and audit reason" >&2; exit 64; }
    begin_release "$command" "$2" "$3" "$4"
    ;;
  status)
    [[ $# -eq 2 ]] || { echo "status requires operation ID" >&2; exit 64; }
    status_release "$2"
    ;;
  finish)
    [[ $# -eq 2 ]] || { echo "finish requires operation ID" >&2; exit 64; }
    finish_release "$2"
    ;;
  abort)
    [[ $# -ge 2 && $# -le 3 ]] || { echo "abort requires operation ID and optional outcome" >&2; exit 64; }
    abort_release "$2" "${3:-failed}"
    ;;
  verify-runtime)
    [[ $# -eq 6 ]] || { echo "verify-runtime requires SHA and four digests" >&2; exit 64; }
    verify_runtime_release "$2" "$3" "$4" "$5" "$6"
    ;;
  *)
    echo "unsupported delivery helper action" >&2
    exit 64
    ;;
esac
