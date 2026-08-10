SHELL := bash

DOCKER ?= docker
GO ?= go
NPM ?= npm
NODE ?= node
PYTHON ?= python3
TOFU ?= tofu

GENERATED_FILES := \
	apps/api/internal/httpapi/openapi.gen.go \
	apps/api/internal/storage/sqlc/application.sql.go \
	apps/api/internal/storage/sqlc/db.go \
	apps/api/internal/storage/sqlc/models.go \
	apps/web/src/api/schema.d.ts

.PHONY: \
	require-docker dev generate generate-check openapi-lint check portfolio-test backend-check \
	frontend-check runtime-check docs-spellcheck e2e infra-check security-check \
	delivery-test delivery-lint deploy rollback backup restore-drill

require-docker:
	@command -v "$(DOCKER)" >/dev/null 2>&1 || { echo "Docker is required but its CLI is not available." >&2; exit 69; }; \
		$(DOCKER) info >/dev/null 2>&1 || { echo "Docker daemon access is required for this target; no suite was run." >&2; exit 69; }

dev: require-docker
	$(DOCKER) compose -f compose.yaml -f compose.local.yaml up --detach --build --wait

generate:
	cd apps/api && $(GO) generate ./...
	$(NPM) --prefix apps/web run generate

generate-check:
	@before=$$(mktemp); after=$$(mktemp); \
		trap 'rm -f "$$before" "$$after"' EXIT; \
		sha256sum $(GENERATED_FILES) >"$$before"; \
		$(MAKE) --no-print-directory generate; \
		sha256sum $(GENERATED_FILES) >"$$after"; \
		diff --unified "$$before" "$$after" >/dev/null || { \
			echo "Generated artefacts drifted; inspect the regenerated files and run make check again." >&2; \
			exit 1; \
		}

openapi-lint:
	$(NPM) exec --yes --package=@redocly/cli@2.43.3 -- redocly lint specs/openapi.yaml

portfolio-test:
	$(PYTHON) -m unittest tests.portfolio.test_task6_contract -v

backend-check:
	cd apps/api && REQUIRE_DOCKER=1 $(GO) test -mod=readonly ./... -count=1
	cd apps/api && $(GO) vet ./...
	cd apps/api && $(GO) build ./...

frontend-check:
	$(NPM) --prefix apps/web run test -- --run
	$(NPM) --prefix apps/web run typecheck
	$(NPM) --prefix apps/web run lint
	$(NPM) --prefix apps/web run spellcheck
	$(NPM) --prefix apps/web run build

runtime-check:
	$(NODE) --test tests/runtime/runtime-config.test.mjs

docs-spellcheck:
	$(NPM) --prefix apps/web exec -- cspell --config apps/web/cspell.json --locale en-GB --no-progress \
		README.md docs/portfolio.md docs/ai/decision-log.md docs/content-review.md

check: require-docker generate-check openapi-lint portfolio-test backend-check frontend-check runtime-check delivery-test docs-spellcheck
	@echo "Local check completed; infrastructure, security, E2E and operational side effects remain explicit targets."

e2e: require-docker
	$(NPM) --prefix tests/e2e run typecheck
	$(NPM) --prefix tests/e2e test

infra-check:
	$(PYTHON) -m unittest infra.tests.test_infra_contract -v
	$(PYTHON) -m unittest infra.tests.test_resolved_production_compose -v
	$(TOFU) fmt -check -recursive infra/tofu
	$(TOFU) -chdir=infra/tofu init -backend=false -input=false
	$(TOFU) -chdir=infra/tofu validate
	$(PYTHON) infra/tofu/verify_provider_lock.py --tofu "$(TOFU)"
	cd infra/ansible && ansible-playbook --syntax-check playbooks/provision.yml --extra-vars @group_vars/all.example.yml --extra-vars @group_vars/secrets.example.yml
	cd infra/ansible && ansible-playbook --syntax-check playbooks/deploy.yml --extra-vars @group_vars/all.example.yml --extra-vars @group_vars/secrets.example.yml --extra-vars release_sha=0123456789abcdef0123456789abcdef01234567
	cd infra/ansible && ansible-lint playbooks
	cd infra/ansible && ANSIBLE_FILTER_PLUGINS=./filter_plugins ansible-playbook ../tests/ansible/policy-convergence.yml
	cd infra/ansible && ANSIBLE_FILTER_PLUGINS=./filter_plugins ansible-playbook ../tests/ansible/policy-convergence.yml

security-check: require-docker
	@images="$$( $(PYTHON) ops/security/validate_runtime_image_inventory.py \
		--repo-root . \
		--inventory ops/security/runtime-image-inventory.json \
		--print-public-images )" || exit $$?; \
		TRIVY_IMAGES="$${images:?runtime image inventory is empty}" bash ops/security/scan.sh

delivery-test:
	$(PYTHON) -m unittest tests.delivery.test_delivery_contract -v

delivery-lint: require-docker
	$(GO) run github.com/rhysd/actionlint/cmd/actionlint@v1.7.12
	$(DOCKER) run --rm --read-only --cap-drop ALL --security-opt no-new-privileges:true \
		--volume "$(CURDIR):/workspace:ro" \
		koalaman/shellcheck:v0.10.0@sha256:2097951f02e735b613f4a34de20c40f937a6c8f18ecb170612c88c34517221fb \
		/workspace/scripts/delivery/release.sh \
		/workspace/scripts/delivery/resolve-images.sh \
		/workspace/scripts/delivery/remote-release.sh \
		/workspace/scripts/delivery/scan-images.sh \
		/workspace/scripts/delivery/migration-compatibility.sh \
		/workspace/scripts/delivery/ssh-command.sh

deploy:
	@test -n "$(RELEASE_SHA)" || { echo "RELEASE_SHA is required" >&2; exit 64; }
	gh workflow run deploy.yml --ref main --field release_sha="$(RELEASE_SHA)"

rollback:
	gh workflow run rollback.yml --ref main

backup: require-docker
	@test -n "$(AGE_RECIPIENT)" || { echo "AGE_RECIPIENT is required" >&2; exit 64; }
	@test -n "$(RELEASE_SHA)" || { echo "RELEASE_SHA is required" >&2; exit 64; }
	@test -n "$(S3_ACCESS_KEY)" || { echo "S3_ACCESS_KEY is required" >&2; exit 64; }
	@test -n "$(S3_ENDPOINT)" || { echo "S3_ENDPOINT is required" >&2; exit 64; }
	@test -n "$(S3_SECRET_KEY)" || { echo "S3_SECRET_KEY is required" >&2; exit 64; }
	$(DOCKER) compose -f compose.yaml -f compose.observability.yaml --profile operations run --rm backup-metrics-init
	$(DOCKER) compose -f compose.yaml -f compose.observability.yaml --profile operations run --rm --no-deps backup

restore-drill: require-docker
	$(PYTHON) tests/ops/run_local_restore_drill.py
