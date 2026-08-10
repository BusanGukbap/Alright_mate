#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

: "${READINESS_TIMEOUT_SECONDS:=120}"
: "${ALLOW_UNSAFE_MIGRATION_ROLLBACK:=false}"
: "${UNSAFE_MIGRATION_REASON:=}"
: "${PRODUCTION_SSH_HOST:?PRODUCTION_SSH_HOST is required}"
: "${PRODUCTION_SSH_USER:?PRODUCTION_SSH_USER is required}"
: "${PRODUCTION_SSH_PRIVATE_KEY:?PRODUCTION_SSH_PRIVATE_KEY is required}"
: "${PRODUCTION_DELIVERY_SSH_USER:?PRODUCTION_DELIVERY_SSH_USER is required}"
: "${PRODUCTION_DELIVERY_SSH_PRIVATE_KEY:?PRODUCTION_DELIVERY_SSH_PRIVATE_KEY is required}"
: "${PRODUCTION_SSH_KNOWN_HOSTS:?PRODUCTION_SSH_KNOWN_HOSTS is required}"
: "${PRODUCTION_RUNTIME_ENV:?PRODUCTION_RUNTIME_ENV is required}"
: "${PRODUCTION_GHCR_USERNAME:?PRODUCTION_GHCR_USERNAME is required}"
: "${PRODUCTION_GHCR_TOKEN:?PRODUCTION_GHCR_TOKEN is required}"
: "${PRODUCTION_BASE_URL:?PRODUCTION_BASE_URL is required}"
: "${GHCR_REPOSITORY_PREFIX:?GHCR_REPOSITORY_PREFIX is required}"

script_directory=$(CDPATH='' cd -- "$(dirname "$0")" && pwd)
repository_root=$(CDPATH='' cd -- "${script_directory}/../.." && pwd)
temporary_parent=${RUNNER_TEMP:-${TMPDIR:-/tmp}}
controller_workdir=$(mktemp -d "${temporary_parent}/alright-mate-delivery.XXXXXX")
known_hosts_file="${controller_workdir}/known_hosts"
operator_private_key_file="${controller_workdir}/operator_key"
delivery_private_key_file="${controller_workdir}/delivery_key"
runtime_environment_file="${controller_workdir}/runtime.env"
inventory_file="${controller_workdir}/inventory.ini"
secret_vars_file="${controller_workdir}/secret-vars.json"
docker_config_directory="${controller_workdir}/docker"
python_bin=${PYTHON_BIN:-python3}
remote_protocol=alright-mate-delivery-v1
release_lock_active=0
registry_login_active=0
mutation_started=0
operation_id=""
finalisation_state=unknown

validate_sha() {
  [[ ${1:-} =~ ^[0-9a-f]{40}$ ]]
}

validate_operation_id() {
  [[ ${1:-} =~ ^[0-9a-f]{32}$ ]]
}

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  set +e
  if (( release_lock_active == 1 )) && validate_operation_id "$operation_id"; then
    if (( mutation_started == 1 )); then
      printf 'delivery_reconciliation=required operation=%s mutation_state=possible\n' \
        "$operation_id" >&2
      echo "the remote operation and lock were preserved after interruption during runtime mutation" >&2
    else
      run_remote abort "$operation_id" controller-exit >/dev/null 2>&1
    fi
  fi
  if (( registry_login_active == 1 )); then
    DOCKER_CONFIG="$docker_config_directory" docker logout ghcr.io >/dev/null 2>&1
  fi
  case "$controller_workdir" in
    "${temporary_parent}"/alright-mate-delivery.*)
      if [[ -d "$controller_workdir" && ! -L "$controller_workdir" ]]; then
        rm -rf -- "$controller_workdir"
      fi
      ;;
  esac
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

validate_inputs() {
  [[ "$PRODUCTION_SSH_HOST" =~ ^[A-Za-z0-9.-]+$ ]] || { echo "production SSH host is invalid" >&2; exit 64; }
  [[ "$PRODUCTION_SSH_USER" =~ ^[a-z_][a-z0-9_-]*$ ]] || { echo "production SSH user is invalid" >&2; exit 64; }
  [[ "$PRODUCTION_DELIVERY_SSH_USER" =~ ^[a-z_][a-z0-9_-]*$ ]] || { echo "production delivery SSH user is invalid" >&2; exit 64; }
  [[ "$PRODUCTION_DELIVERY_SSH_USER" != "$PRODUCTION_SSH_USER" ]] || {
    echo "Ansible operator and constrained delivery SSH users must be different" >&2
    exit 64
  }
  [[ "$GHCR_REPOSITORY_PREFIX" =~ ^ghcr\.io/[a-z0-9]+([._-][a-z0-9]+)*(/[a-z0-9]+([._-][a-z0-9]+)*)+$ ]] || {
    echo "GHCR repository prefix is invalid" >&2
    exit 64
  }
  [[ "$PRODUCTION_BASE_URL" =~ ^https://[A-Za-z0-9.-]+(:[0-9]+)?$ ]] || {
    echo "production base URL must be an HTTPS origin without a path" >&2
    exit 64
  }
  if [[ ! "$READINESS_TIMEOUT_SECONDS" =~ ^[0-9]+$ ]] ||
    (( READINESS_TIMEOUT_SECONDS < 30 || READINESS_TIMEOUT_SECONDS > 300 )); then
    echo "readiness timeout must be between 30 and 300 seconds" >&2
    exit 64
  fi
  [[ "$ALLOW_UNSAFE_MIGRATION_ROLLBACK" == "true" || "$ALLOW_UNSAFE_MIGRATION_ROLLBACK" == "false" ]] || {
    echo "unsafe migration rollback override must be true or false" >&2
    exit 64
  }
  if [[ "$ALLOW_UNSAFE_MIGRATION_ROLLBACK" == "true" ]]; then
    if (( ${#UNSAFE_MIGRATION_REASON} < 12 || ${#UNSAFE_MIGRATION_REASON} > 240 )) ||
      [[ "$UNSAFE_MIGRATION_REASON" == *$'\n'* || "$UNSAFE_MIGRATION_REASON" == *$'\r'* ]]; then
      echo "unsafe migration rollback override requires a 12-240 character single-line reason" >&2
      exit 64
    fi
  elif [[ -n "$UNSAFE_MIGRATION_REASON" ]]; then
    echo "unsafe migration reason is only accepted with the protected override" >&2
    exit 64
  fi
}

prepare_secret_files() {
  printf '%s\n' "$PRODUCTION_SSH_KNOWN_HOSTS" >"$known_hosts_file"
  printf '%s\n' "$PRODUCTION_SSH_PRIVATE_KEY" >"$operator_private_key_file"
  printf '%s\n' "$PRODUCTION_DELIVERY_SSH_PRIVATE_KEY" >"$delivery_private_key_file"
  printf '%s\n' "$PRODUCTION_RUNTIME_ENV" >"$runtime_environment_file"
  chmod 0600 "$known_hosts_file" "$operator_private_key_file" \
    "$delivery_private_key_file" "$runtime_environment_file"
  ssh-keygen -F "$PRODUCTION_SSH_HOST" -f "$known_hosts_file" >/dev/null || {
    echo "known_hosts does not contain the production SSH host" >&2
    exit 65
  }

  printf '[alright_mate]\nalright-mate ansible_host=%s ansible_user=%s ansible_ssh_private_key_file=%s\n' \
    "$PRODUCTION_SSH_HOST" "$PRODUCTION_SSH_USER" "$operator_private_key_file" >"$inventory_file"
  "$python_bin" - "$secret_vars_file" <<'PY'
import json
import os
import sys

with open(sys.argv[1], "w", encoding="utf-8") as stream:
    json.dump(
        {
            "ghcr_username": os.environ["PRODUCTION_GHCR_USERNAME"],
            "ghcr_token": os.environ["PRODUCTION_GHCR_TOKEN"],
        },
        stream,
    )
PY
  chmod 0600 "$inventory_file" "$secret_vars_file"
  mkdir "$docker_config_directory"
  chmod 0700 "$controller_workdir" "$docker_config_directory"
}

ssh_options=(
  -o BatchMode=yes
  -o IdentitiesOnly=yes
  -o StrictHostKeyChecking=yes
  -o "UserKnownHostsFile=${known_hosts_file}"
  -i "$delivery_private_key_file"
)

encode_remote_arguments() {
  "$python_bin" - "$@" <<'PY'
import base64
import sys

arguments = [argument.encode("utf-8") for argument in sys.argv[1:]]
sys.stdout.write(base64.b64encode(b"\0".join(arguments) + b"\0").decode("ascii"))
PY
}

run_remote() {
  local payload_token
  payload_token=$(encode_remote_arguments "$@")
  [[ "$payload_token" =~ ^[A-Za-z0-9+/]+={0,2}$ ]] || {
    echo "delivery SSH payload encoding failed" >&2
    return 65
  }
  # The fixed protocol literal and generated base64 token are expanded on the trusted controller.
  # shellcheck disable=SC2029
  ssh "${ssh_options[@]}" "${PRODUCTION_DELIVERY_SSH_USER}@${PRODUCTION_SSH_HOST}" \
    "${remote_protocol} ${payload_token}"
}

new_operation_id() {
  local candidate=${DELIVERY_OPERATION_ID:-}
  if [[ -z "$candidate" ]]; then
    candidate=$("$python_bin" -c 'import secrets; print(secrets.token_hex(16))')
  fi
  validate_operation_id "$candidate" || {
    echo "generated delivery operation ID is invalid" >&2
    return 65
  }
  printf '%s\n' "$candidate"
}

prepare_runtime_config() {
  local release_sha=$1
  local destination=$2
  validate_sha "$release_sha" || { echo "release SHA is invalid" >&2; return 64; }
  git -C "$repository_root" cat-file -e "${release_sha}^{commit}"
  git -C "$repository_root" merge-base --is-ancestor "$release_sha" origin/main || {
    echo "release SHA is not reachable from protected main" >&2
    return 65
  }
  mkdir -p "$destination"
  git -C "$repository_root" archive "$release_sha" | tar -x -C "$destination"
}

resolve_release_images() {
  local release_sha=$1
  local destination=$2
  DOCKER_CONFIG="$docker_config_directory" \
    PYTHON_BIN="$python_bin" \
    GHCR_REPOSITORY_PREFIX="$GHCR_REPOSITORY_PREFIX" \
    bash "${script_directory}/resolve-images.sh" "$release_sha" >"$destination"
}

output_value() {
  local key=$1
  local content=$2
  printf '%s\n' "$content" | awk -F= -v wanted="$key" '$1 == wanted { print substr($0, index($0, "=") + 1); exit }'
}

file_value() {
  local key=$1
  local file=$2
  awk -F= -v wanted="$key" '$1 == wanted { print substr($0, index($0, "=") + 1); exit }' "$file"
}

image_reference() {
  local key=$1
  local file=$2
  local value
  value=$(file_value "$key" "$file")
  [[ "$value" =~ ^ghcr\.io/[a-z0-9]+([._/-][a-z0-9]+)*@sha256:[0-9a-f]{64}$ ]] || {
    echo "resolved ${key} is invalid" >&2
    return 65
  }
  printf '%s\n' "$value"
}

write_operation_references() {
  local output=$1
  local prefix=$2
  local destination=$3
  local api
  local migration
  local web
  local backup
  api=$(output_value "${prefix}_api_image_reference" "$output")
  migration=$(output_value "${prefix}_migration_image_reference" "$output")
  web=$(output_value "${prefix}_web_image_reference" "$output")
  backup=$(output_value "${prefix}_backup_image_reference" "$output")
  printf 'api_image_reference=%s\nmigration_image_reference=%s\nweb_image_reference=%s\nbackup_image_reference=%s\n' \
    "$api" "$migration" "$web" "$backup" >"$destination"
  image_reference api_image_reference "$destination" >/dev/null
  image_reference migration_image_reference "$destination" >/dev/null
  image_reference web_image_reference "$destination" >/dev/null
  image_reference backup_image_reference "$destination" >/dev/null
}

run_ansible_release() {
  local release_sha=$1
  local runtime_config=$2
  local references=$3
  local api_image
  local migration_image
  local web_image
  local backup_image

  api_image=$(image_reference api_image_reference "$references")
  migration_image=$(image_reference migration_image_reference "$references")
  web_image=$(image_reference web_image_reference "$references")
  backup_image=$(image_reference backup_image_reference "$references")
  ANSIBLE_FILTER_PLUGINS="${repository_root}/infra/ansible/filter_plugins" \
  ANSIBLE_HOST_KEY_CHECKING=True \
  ANSIBLE_HOST_PATTERN_MISMATCH=error \
  ANSIBLE_SSH_ARGS="-o StrictHostKeyChecking=yes -o UserKnownHostsFile=${known_hosts_file} -o IdentitiesOnly=yes" \
    ansible-playbook \
      --inventory "$inventory_file" \
      "${repository_root}/infra/ansible/playbooks/deploy.yml" \
      --extra-vars "@${secret_vars_file}" \
      --extra-vars "alright_mate_deploy_user=${PRODUCTION_DELIVERY_SSH_USER}" \
      --extra-vars "alright_mate_deploy_group=alright-mate" \
      --extra-vars "release_sha=${release_sha}" \
      --extra-vars "runtime_config_source=${runtime_config}" \
      --extra-vars "runtime_environment_file=${runtime_environment_file}" \
      --extra-vars "api_image_reference=${api_image}" \
      --extra-vars "migration_image_reference=${migration_image}" \
      --extra-vars "web_image_reference=${web_image}" \
      --extra-vars "backup_image_reference=${backup_image}"
}

verify_runtime_release() {
  local release_sha=$1
  local references=$2
  local api_image
  local migration_image
  local web_image
  local backup_image

  api_image=$(image_reference api_image_reference "$references")
  migration_image=$(image_reference migration_image_reference "$references")
  web_image=$(image_reference web_image_reference "$references")
  backup_image=$(image_reference backup_image_reference "$references")
  run_remote verify-runtime "$release_sha" "$api_image" "$migration_image" \
    "$web_image" "$backup_image"
}

wait_for_readiness() {
  local deadline=$(( $(date +%s) + READINESS_TIMEOUT_SECONDS ))
  while (( $(date +%s) < deadline )); do
    if curl --proto '=https' --tlsv1.2 --fail --silent --output /dev/null \
        --connect-timeout 5 --max-time 10 "${PRODUCTION_BASE_URL}/healthz" && \
       curl --proto '=https' --tlsv1.2 --fail --silent --output /dev/null \
        --connect-timeout 5 --max-time 10 "${PRODUCTION_BASE_URL}/readyz" && \
       curl --proto '=https' --tlsv1.2 --fail --silent --output /dev/null \
        --connect-timeout 5 --max-time 10 "${PRODUCTION_BASE_URL}/api/v1/expressions?limit=1"; then
      printf 'release_smoke=success\n'
      return 0
    fi
    sleep 2
  done
  echo "release readiness and public smoke timed out" >&2
  return 1
}

begin_with_reconciliation() {
  local expected_operation_id=$1
  shift
  local output
  local status
  if output=$(run_remote "$@"); then
    :
  elif ! output=$(run_remote status "$expected_operation_id"); then
    echo "delivery begin failed and its remote state could not be reconciled" >&2
    return 1
  fi
  status=$(output_value operation_status "$output")
  [[ "$status" == "pending" || "$status" == "success" ]] || {
    echo "delivery operation is not eligible to continue" >&2
    return 1
  }
  printf '%s\n' "$output"
}

finish_with_reconciliation() {
  local expected_operation_id=$1
  local output
  local status
  local attempt
  finalisation_state=unknown
  for attempt in 1 2; do
    if output=$(run_remote finish "$expected_operation_id"); then
      status=$(output_value operation_status "$output")
      if [[ "$status" == "success" ]]; then
        finalisation_state=success
        return 0
      fi
    fi
    if output=$(run_remote status "$expected_operation_id"); then
      status=$(output_value operation_status "$output")
      if [[ "$status" == "success" ]]; then
        finalisation_state=success
        return 0
      fi
      if [[ "$status" == "pending" ]]; then
        if (( attempt == 2 )); then
          finalisation_state=confirmed-pending
          echo "release finalisation state=confirmed-pending" >&2
          return 1
        fi
        continue
      fi
      break
    fi
  done
  finalisation_state=unknown
  echo "release finalisation state=unknown" >&2
  return 1
}

preserve_unknown_finalisation() {
  local expected_operation_id=$1
  local target_sha=$2
  release_lock_active=0
  printf 'delivery_reconciliation=required finalisation_state=unknown operation=%s target_runtime=%s\n' \
    "$expected_operation_id" "$target_sha"
  echo "target runtime and remote operation state were preserved for operator reconciliation" >&2
}

abort_with_reconciliation() {
  local expected_operation_id=$1
  local outcome=$2
  local output
  local status
  local _
  for _ in 1 2; do
    if output=$(run_remote abort "$expected_operation_id" "$outcome"); then
      status=$(output_value operation_status "$output")
      [[ "$status" == "aborted" ]] && return 0
    fi
    if output=$(run_remote status "$expected_operation_id"); then
      status=$(output_value operation_status "$output")
      [[ "$status" == "aborted" ]] && return 0
      [[ "$status" == "pending" ]] || break
    fi
  done
  echo "release abort could not be reconciled" >&2
  return 1
}

preserve_failed_reconciliation() {
  local expected_operation_id=$1
  local runtime_state=$2
  local runtime_sha=$3
  release_lock_active=0
  printf 'delivery_reconciliation=required operation=%s runtime_state=%s runtime=%s\n' \
    "$expected_operation_id" "$runtime_state" "$runtime_sha"
  echo "remote operation state was preserved for an explicit operator retry" >&2
}

preserve_preparing_reconciliation() {
  local expected_operation_id=$1
  release_lock_active=0
  printf 'delivery_reconciliation=required operation=%s state=preparing\n' \
    "$expected_operation_id"
  echo "delivery preparation may still own the remote lock; retry with the same operation ID" >&2
}

rollback_after_failure() {
  local failed_operation_id=$1
  local failed_sha=$2
  local previous_sha=$3
  local previous_config=$4
  local previous_references=$5
  local outcome=$6
  echo "release failed after possible mutation; restoring the recorded previous immutable digests without database rollback" >&2
  if run_ansible_release "$previous_sha" "$previous_config" "$previous_references" && \
      verify_runtime_release "$previous_sha" "$previous_references" && wait_for_readiness; then
    if abort_with_reconciliation "$failed_operation_id" "$outcome"; then
      release_lock_active=0
      printf 'automatic_image_rollback=success database_rollback=none failed=%s previous=%s\n' \
        "$failed_sha" "$previous_sha"
      return 0
    fi
    preserve_failed_reconciliation "$failed_operation_id" restored-abort-unknown "$previous_sha"
  else
    preserve_failed_reconciliation "$failed_operation_id" restoration-unknown "$previous_sha"
    echo "automatic image rollback failed; database rollback was not attempted" >&2
    return 1
  fi
  echo "previous runtime was restored, but release state reconciliation failed" >&2
  return 1
}

deploy_release() {
  local target_sha=$1
  local target_config="${controller_workdir}/target-config"
  local target_references="${controller_workdir}/target-images.env"
  local previous_config="${controller_workdir}/previous-config"
  local previous_references="${controller_workdir}/previous-images.env"
  local begin_output
  local begin_command=begin-deploy
  local operation_status
  local initial_deploy
  local previous_sha
  local target_api
  local target_migration
  local target_web
  local target_backup
  local migration_rollback_safe

  validate_sha "$target_sha" || { echo "release SHA is invalid" >&2; return 64; }
  prepare_runtime_config "$target_sha" "$target_config"
  resolve_release_images "$target_sha" "$target_references"
  target_api=$(image_reference api_image_reference "$target_references")
  target_migration=$(image_reference migration_image_reference "$target_references")
  target_web=$(image_reference web_image_reference "$target_references")
  target_backup=$(image_reference backup_image_reference "$target_references")
  migration_rollback_safe=$(file_value migration_rollback_safe "$target_references")
  [[ "$migration_rollback_safe" == "true" || "$migration_rollback_safe" == "false" ]] || {
    echo "eligible release has an invalid migration compatibility declaration" >&2
    return 65
  }
  if [[ "$migration_rollback_safe" != "true" && "$ALLOW_UNSAFE_MIGRATION_ROLLBACK" != "true" ]]; then
    echo "release migration is not declared safe for automatic application rollback" >&2
    return 65
  fi

  operation_id=$(new_operation_id)
  printf 'delivery_operation=%s\n' "$operation_id"
  release_lock_active=1
  if ! begin_output=$(begin_with_reconciliation "$operation_id" "$begin_command" "$operation_id" \
      "$target_sha" "$target_api" "$target_migration" "$target_web" "$target_backup" "$migration_rollback_safe" \
      "$ALLOW_UNSAFE_MIGRATION_ROLLBACK" "$UNSAFE_MIGRATION_REASON"); then
    preserve_preparing_reconciliation "$operation_id"
    return 1
  fi
  operation_status=$(output_value operation_status "$begin_output")
  if [[ "$operation_status" == "success" ]]; then
    release_lock_active=0
    printf 'deployment=idempotent-success current=%s operation=%s\n' "$target_sha" "$operation_id"
    return 0
  fi
  initial_deploy=$(output_value initial_deploy "$begin_output")
  [[ "$initial_deploy" == "true" || "$initial_deploy" == "false" ]] || {
    echo "remote initial deployment state is invalid" >&2
    return 65
  }
  previous_sha=$(output_value previous_sha "$begin_output")
  validate_sha "$previous_sha" || { echo "remote previous release is invalid" >&2; return 65; }
  if [[ "$initial_deploy" != "true" ]]; then
    write_operation_references "$begin_output" previous "$previous_references"
    prepare_runtime_config "$previous_sha" "$previous_config"
  fi

  mutation_started=1
  if run_ansible_release "$target_sha" "$target_config" "$target_references" && \
      verify_runtime_release "$target_sha" "$target_references" && wait_for_readiness; then
    if finish_with_reconciliation "$operation_id"; then
      release_lock_active=0
      printf 'deployment=success current=%s previous=%s operation=%s\n' \
        "$target_sha" "$previous_sha" "$operation_id"
      return 0
    fi
    if [[ "$initial_deploy" == "true" ]]; then
      preserve_failed_reconciliation "$operation_id" initial-deploy-finalisation "$target_sha"
    elif [[ "$finalisation_state" == "confirmed-pending" ]]; then
      rollback_after_failure "$operation_id" "$target_sha" "$previous_sha" \
        "$previous_config" "$previous_references" finalisation-failed
    else
      preserve_unknown_finalisation "$operation_id" "$target_sha"
    fi
    return 1
  fi
  if [[ "$initial_deploy" == "true" ]]; then
    preserve_failed_reconciliation "$operation_id" initial-deploy-incomplete "$target_sha"
    return 1
  fi
  rollback_after_failure "$operation_id" "$target_sha" "$previous_sha" \
    "$previous_config" "$previous_references" automatic-image-rollback
  return 1
}

rollback_release() {
  local begin_output
  local begin_command=begin-rollback
  local operation_status
  local target_sha
  local previous_sha
  local target_config="${controller_workdir}/rollback-config"
  local target_references="${controller_workdir}/rollback-images.env"
  local recovery_config="${controller_workdir}/recovery-config"
  local recovery_references="${controller_workdir}/recovery-images.env"

  operation_id=$(new_operation_id)
  printf 'delivery_operation=%s\n' "$operation_id"
  release_lock_active=1
  if ! begin_output=$(begin_with_reconciliation "$operation_id" "$begin_command" "$operation_id" \
      "$ALLOW_UNSAFE_MIGRATION_ROLLBACK" "$UNSAFE_MIGRATION_REASON"); then
    preserve_preparing_reconciliation "$operation_id"
    return 1
  fi
  operation_status=$(output_value operation_status "$begin_output")
  target_sha=$(output_value target_sha "$begin_output")
  previous_sha=$(output_value previous_sha "$begin_output")
  if ! validate_sha "$target_sha" || ! validate_sha "$previous_sha"; then
    echo "recorded rollback state is invalid" >&2
    return 65
  fi
  if [[ "$operation_status" == "success" ]]; then
    release_lock_active=0
    printf 'rollback=idempotent-success current=%s operation=%s\n' "$target_sha" "$operation_id"
    return 0
  fi
  write_operation_references "$begin_output" target "$target_references"
  write_operation_references "$begin_output" previous "$recovery_references"
  prepare_runtime_config "$target_sha" "$target_config"
  prepare_runtime_config "$previous_sha" "$recovery_config"

  mutation_started=1
  if run_ansible_release "$target_sha" "$target_config" "$target_references" && \
      verify_runtime_release "$target_sha" "$target_references" && wait_for_readiness; then
    if finish_with_reconciliation "$operation_id"; then
      release_lock_active=0
      printf 'rollback=success current=%s previous=%s operation=%s database_rollback=none\n' \
        "$target_sha" "$previous_sha" "$operation_id"
      return 0
    fi
    if [[ "$finalisation_state" == "confirmed-pending" ]]; then
      rollback_after_failure "$operation_id" "$target_sha" "$previous_sha" \
        "$recovery_config" "$recovery_references" finalisation-failed
    else
      preserve_unknown_finalisation "$operation_id" "$target_sha"
    fi
    return 1
  fi
  rollback_after_failure "$operation_id" "$target_sha" "$previous_sha" \
    "$recovery_config" "$recovery_references" automatic-image-rollback
  return 1
}

validate_inputs
prepare_secret_files
printf '%s' "$PRODUCTION_GHCR_TOKEN" | DOCKER_CONFIG="$docker_config_directory" \
  docker login ghcr.io --username "$PRODUCTION_GHCR_USERNAME" --password-stdin >/dev/null
registry_login_active=1
[[ -f "${docker_config_directory}/config.json" ]] || {
  echo "isolated production GHCR credential file was not created" >&2
  exit 70
}
chmod 0600 "${docker_config_directory}/config.json"

case ${1:-} in
  deploy)
    [[ $# -eq 2 ]] || { echo "deploy requires one release SHA" >&2; exit 64; }
    deploy_release "$2"
    ;;
  rollback)
    [[ $# -eq 1 ]] || { echo "rollback does not accept a caller-selected SHA" >&2; exit 64; }
    rollback_release
    ;;
  *)
    echo "usage: release.sh deploy RELEASE_SHA|rollback" >&2
    exit 64
    ;;
esac
