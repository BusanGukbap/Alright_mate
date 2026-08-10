#!/bin/sh
set -eu

script_directory=$(CDPATH='' cd -- "$(dirname "$0")" && pwd)
ansible_root=$(CDPATH='' cd -- "${script_directory}/../../ansible" && pwd)
controller_inventory=$(mktemp)

cleanup() {
  rm -f -- "$controller_inventory"
}
trap cleanup EXIT HUP INT TERM

printf '[alright_mate]\nalright-mate ansible_connection=local\n' >"$controller_inventory"
ANSIBLE_CONFIG="${ansible_root}/ansible.cfg" \
ANSIBLE_FILTER_PLUGINS="${ansible_root}/filter_plugins" \
ANSIBLE_HOST_PATTERN_MISMATCH=error \
  ansible-playbook --inventory "$controller_inventory" \
    "${script_directory}/delivery-controller-context.yml"
