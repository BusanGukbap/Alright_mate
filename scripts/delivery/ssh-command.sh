#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

readonly protocol_prefix='alright-mate-delivery-v1 '
readonly delivery_helper=/usr/local/libexec/alright-mate-delivery
payload_file=""

cleanup() {
  if [[ -n "$payload_file" ]]; then
    rm -f -- "$payload_file"
  fi
}

reject_command() {
  echo "delivery SSH command was rejected" >&2
  exit 64
}

trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

original_command=${SSH_ORIGINAL_COMMAND:-}
[[ "$original_command" == "${protocol_prefix}"* ]] || reject_command
payload_token=${original_command#"$protocol_prefix"}
[[ "$original_command" == "${protocol_prefix}${payload_token}" ]] || reject_command
[[ "$payload_token" =~ ^[A-Za-z0-9+/]+={0,2}$ ]] || reject_command

payload_file=$(mktemp /tmp/alright-mate-delivery-ssh.XXXXXX)
if ! printf '%s' "$payload_token" | base64 --decode >"$payload_file"; then
  reject_command
fi

payload_size=$(wc -c <"$payload_file")
(( payload_size > 0 )) || reject_command
last_byte=$(od -An -t u1 -j "$(( payload_size - 1 ))" -N 1 "$payload_file" | tr -d '[:space:]')
[[ "$last_byte" == "0" ]] || reject_command

mapfile -d '' -t helper_arguments <"$payload_file"
case "${helper_arguments[0]:-}" in
  begin-deploy)
    (( ${#helper_arguments[@]} == 10 )) || reject_command
    ;;
  begin-rollback)
    (( ${#helper_arguments[@]} == 4 )) || reject_command
    ;;
  status | finish)
    (( ${#helper_arguments[@]} == 2 )) || reject_command
    ;;
  abort)
    (( ${#helper_arguments[@]} == 2 || ${#helper_arguments[@]} == 3 )) || reject_command
    ;;
  verify-runtime)
    (( ${#helper_arguments[@]} == 6 )) || reject_command
    ;;
  *)
    reject_command
    ;;
esac

exec sudo -n "$delivery_helper" "${helper_arguments[@]}"
