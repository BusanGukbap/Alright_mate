# Ansible host and release automation

<!-- cspell:words opencontainers vars -->

The playbooks provision an Ubuntu LTS Docker host and deploy one immutable commit-SHA release. They do not create cloud resources, mutate DNS or contain credentials.

## Controller setup

Create a workspace-local virtual environment, install `requirements.txt`, then install the exact collections in `requirements.yml`. Native Windows is not a supported Ansible control node; use a Linux CI runner or WSL when syntax-checking or running these playbooks.

Copy `inventories/production.example.ini` to the ignored `inventory.ini`. Copy `group_vars/secrets.example.yml` to the ignored `secrets.yml`, fill the named values and protect it with Ansible Vault or an equivalent secret store. Do not commit SSH keys, GHCR credentials or runtime environment files.

`group_vars/all.example.yml` documents the non-secret inputs. Supply its values through reviewed inventory variables. The example address and CIDR are documentation ranges and cannot identify a live host. The API, migration, web and backup-tools image inputs must be lower-case `ghcr.io` references pinned by `@sha256:` digest; tags and opaque image strings are rejected.

## Static validation

```text
ansible-galaxy collection install --requirements-file requirements.yml
ansible-playbook --syntax-check playbooks/provision.yml --extra-vars @group_vars/all.example.yml --extra-vars @group_vars/secrets.example.yml
ansible-playbook --syntax-check playbooks/deploy.yml --extra-vars @group_vars/all.example.yml --extra-vars @group_vars/secrets.example.yml --extra-vars release_sha=0123456789abcdef0123456789abcdef01234567
ansible-lint playbooks
ANSIBLE_FILTER_PLUGINS=./filter_plugins ansible-playbook ../tests/ansible/policy-convergence.yml
ANSIBLE_FILTER_PLUGINS=./filter_plugins ansible-playbook ../tests/ansible/policy-convergence.yml
```

Syntax checks do not connect to the example inventory. The policy-evaluation playbook is deliberately local and makes no host changes: it exercises CIDR normalisation, UFW drift detection (including a misleading managed comment over the wrong actual source) and the no-reset second evaluation twice. It does not prove host convergence. A real run requires a user-approved static IP, strict host-key material, an operator key, restricted CIDRs and secret injection.

## Provision and deploy boundaries

`provision.yml` installs Docker Engine from Docker's signed Ubuntu repository, verifies that the downloaded key contains exactly one primary key with the approved `9DC858229FC7DD38854AE2D88D81803C0EBFCD88` fingerprint before enabling the repository, configures bounded logs, creates the dedicated deployment account and directory hierarchy, enables unattended security upgrades, restricts SSH, and mirrors the Lightsail firewall at UFW. It also creates the locked, non-interactive `alright-mate-backup` account and its protected `/etc/alright-mate` configuration directory. The account's supplementary groups converge to exactly the unprivileged deployment group, removing any drifted Docker or operator-added membership while preserving read/traverse access to release directories; each release `.env` remains owner-only mode `0600`. The backup systemd service adds `SupplementaryGroups=docker` only for that unit. Docker access is root-equivalent and must remain confined to the reviewed unit. This playbook does not install or enable the later operations task's systemd units or timers.

Operator SSH sources are parsed with Python's canonical IPv4/IPv6 CIDR rules. Host addresses without a prefix, host bits in a network, `any`, malformed strings, duplicate spellings and every prefix-zero world route are rejected or normalised before UFW is touched. UFW rules carry managed comments, but drift detection separately parses and canonicalises each SSH rule's actual source column and compares it directly with the approved CIDR. A misleading comment cannot approve a different source. If the current active rule set contains a world-open or unmanaged rule, provisioning resets it while UFW is disabled, restores the validated SSH CIDRs first, adds public 80/443, and enables the firewall only after the desired rules exist. An already converged second run does not reset UFW.

`deploy.yml` requires the versioned runtime directory produced by the runtime task, an ignored environment file, injected GHCR credentials, three application image digests, one backup-tools auxiliary digest and a 40-character lower-case commit SHA. The environment file may contain runtime secrets and settings, but must not define `API_IMAGE`, `MIGRATION_IMAGE`, `WEB_IMAGE`, `BACKUP_TOOLS_IMAGE`, `BUILD_VERSION`, `RELEASE_SHA` or `IMAGE_TAG`; Ansible owns those release-identity values. It copies configuration into a SHA-specific release directory, renders `compose.yaml` plus `compose.production.yaml`, and asks that exact resolved project for its complete image set. Every resolved PostgreSQL, gateway, OAuth and application reference must be a unique immutable digest before Ansible pre-pulls it. This makes Compose's later `pull: never` safe on a freshly provisioned host rather than relying on an existing local cache.

After the complete production set is present, the playbook ensures the backup-tools auxiliary is present, inspects all four first-party images, and requires every `org.opencontainers.image.revision` label to equal the release SHA. It also resolves the `backup-metrics-init` public image directly from the versioned `compose.observability.yaml`, rejects a mutable reference and ensures its exact digest is present. Digest-addressed images already covered by the resolved production pull are not downloaded twice. Compose still receives and deploys only the three application images, reusing verified local digests with `pull: never`. After successful reconciliation, Ansible atomically writes `/etc/alright-mate/backup-release.env` as `root:alright-mate-backup` mode `0640`; that file contains only `RELEASE_SHA` and `BACKUP_TOOLS_IMAGE`, never credentials. The `current` symlink advances only after both reconciliation and durable backup identity installation succeed. Running the same playbook with the previous SHA and image digests rewrites both application state and backup identity during rollback.

The task's approved verification boundary remains static. The local policy-evaluation fixture exercises deterministic filters without UFW, systemd, Docker Engine or AWS mutation; it is not a host-convergence run. A true twice-run host convergence test requires a disposable Ubuntu VM with systemd, UFW and a preserved management connection, so it is intentionally deferred to an explicitly approved acceptance environment rather than simulated as a container success.
