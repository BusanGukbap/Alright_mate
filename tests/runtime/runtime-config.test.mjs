import assert from "node:assert/strict";
import { after, describe, test } from "node:test";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const testDirectory = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(testDirectory, "..", "..");
const dockerConfig = mkdtempSync(path.join(tmpdir(), "alright-mate-docker-config-"));
const nginxImage =
  "nginx:1.30.4-alpine3.24-slim@sha256:ddde39c6e51f02fde7410c2e9c234cf2d0a4c7bdbbe176aeb37d8ad7ab4eb58c";
const postgresImage =
  "cgr.dev/chainguard/postgres:latest@sha256:e67e686bf76586ff0b87fd42553f2dce5e461cb76e090f502ab129aaef141f12";
const oauthImage =
  "quay.io/oauth2-proxy/oauth2-proxy-nightly:latest@sha256:522b5685d761526dcae84cc0bef0037475749608a3564a2c9314db563cd8f099";

after(() => rmSync(dockerConfig, { recursive: true, force: true }));

const safeEnvironment = {
  ...process.env,
  DOCKER_CONFIG: dockerConfig,
  COMPOSE_PROJECT_NAME: "alright-mate-runtime-test",
  APP_HTTP_PORT: "18080",
  POSTGRES_DB: "alrightmate",
  POSTGRES_USER: "alrightmate",
  POSTGRES_PASSWORD: "non-secret-test-password",
  DATABASE_URL:
    "postgres://alrightmate:non-secret-test-password@db:5432/alrightmate?sslmode=disable",
  OWNER_GITHUB_LOGIN: "test-owner",
  BUILD_VERSION: "task-4a-test",
  IMAGE_TAG: "task-4a-test",
  SERVER_NAME: "example.invalid",
  AUTH_SUBNET: "172.30.0.0/24",
  AUTH_GATEWAY_IP: "172.30.0.10",
  API_IMAGE:
    "registry.invalid/alright-mate-api@sha256:1111111111111111111111111111111111111111111111111111111111111111",
  MIGRATION_IMAGE:
    "registry.invalid/alright-mate-migration@sha256:2222222222222222222222222222222222222222222222222222222222222222",
  WEB_IMAGE:
    "registry.invalid/alright-mate-web@sha256:3333333333333333333333333333333333333333333333333333333333333333",
  TLS_RUNTIME_DIRECTORY: path.join(testDirectory, "fixtures", "tls"),
  ACME_WEBROOT_PATH: testDirectory,
  OAUTH2_PROXY_CLIENT_ID: "dummy-client-id",
  OAUTH2_PROXY_CLIENT_SECRET: "dummy-client-secret",
  OAUTH2_PROXY_COOKIE_SECRET: "QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUE=",
};

function projectFile(relativePath) {
  return path.join(projectRoot, relativePath);
}

function readProjectFile(relativePath) {
  return readFileSync(projectFile(relativePath), "utf8");
}

function renderCompose(mode, environment = safeEnvironment) {
  const result = spawnSync(
    "docker",
    [
      "compose",
      "--project-directory",
      projectRoot,
      "-f",
      projectFile("compose.yaml"),
      "-f",
      projectFile(`compose.${mode}.yaml`),
      "config",
      "--format",
      "json",
    ],
    {
      cwd: projectRoot,
      env: environment,
      encoding: "utf8",
      maxBuffer: 20 * 1024 * 1024,
    },
  );

  assert.equal(
    result.status,
    0,
    `docker compose config (${mode}) failed:\n${result.stderr || result.stdout}`,
  );
  return JSON.parse(result.stdout);
}

function renderStandaloneCompose(relativePath, environment = safeEnvironment) {
  const result = spawnSync(
    "docker",
    [
      "compose",
      "--project-directory",
      projectRoot,
      "-f",
      projectFile(relativePath),
      "config",
      "--format",
      "json",
    ],
    {
      cwd: projectRoot,
      env: environment,
      encoding: "utf8",
      maxBuffer: 20 * 1024 * 1024,
    },
  );
  assert.equal(
    result.status,
    0,
    `docker compose config (${relativePath}) failed:\n${result.stderr || result.stdout}`,
  );
  return JSON.parse(result.stdout);
}

function locationBlock(configuration, marker) {
  const start = configuration.indexOf(marker);
  assert.notEqual(start, -1, `missing Nginx location marker: ${marker}`);
  const open = configuration.indexOf("{", start);
  assert.notEqual(open, -1, `location has no opening brace: ${marker}`);

  let depth = 1;
  for (let index = open + 1; index < configuration.length; index += 1) {
    if (configuration[index] === "{") depth += 1;
    if (configuration[index] === "}") depth -= 1;
    if (depth === 0) return configuration.slice(open + 1, index);
  }
  assert.fail(`location has no closing brace: ${marker}`);
}

function assertRuntimeHardening(serviceName, service) {
  assert.equal(service.read_only, true, `${serviceName} root filesystem must be read-only`);
  assert.ok(service.cap_drop?.includes("ALL"), `${serviceName} must drop all capabilities`);
  assert.ok(
    service.security_opt?.includes("no-new-privileges=true"),
    `${serviceName} must set no-new-privileges`,
  );
  assert.equal(service.logging?.driver, "json-file", `${serviceName} must use bounded JSON logs`);
  assert.equal(service.logging?.options?.["max-size"], "10m");
  assert.equal(service.logging?.options?.["max-file"], "3");
}

describe("Compose topology", () => {
  for (const mode of ["local", "ci", "production"]) {
    test(`${mode} renders and only the gateway publishes host ports`, () => {
      const configuration = renderCompose(mode);
      const publishedServices = Object.entries(configuration.services)
        .filter(([, service]) => (service.ports ?? []).length > 0)
        .map(([name]) => name);

      assert.deepEqual(publishedServices, ["gateway"]);
      assert.equal(configuration.services.api.ports, undefined);
      assert.equal(configuration.services.db.ports, undefined);
      assert.equal(configuration.networks.backend.internal, true);
      assert.equal(configuration.networks.api_gateway.internal, true);
      assert.equal(configuration.networks.web_gateway.internal, true);
      assert.notEqual(configuration.networks.edge.internal, true);
      const membersOf = (networkName) =>
        Object.entries(configuration.services)
          .filter(([, service]) => Object.hasOwn(service.networks ?? {}, networkName))
          .map(([name]) => name)
          .sort();
      assert.deepEqual(membersOf("api_gateway"), ["api", "gateway"]);
      assert.deepEqual(membersOf("web_gateway"), ["gateway", "web"]);
      assert.deepEqual(membersOf("edge"), ["gateway"]);
      assert.equal(Object.hasOwn(configuration.services.web.networks, "api_gateway"), false);
      assert.equal(Object.hasOwn(configuration.services.web.networks, "backend"), false);
      assert.ok(configuration.volumes["postgres-data"]);
    });
  }

  test("migration completion and health gates precede API and gateway readiness", () => {
    const configuration = renderCompose("local");

    assert.equal(configuration.services.migrate.depends_on.db.condition, "service_healthy");
    assert.equal(
      configuration.services.api.depends_on.migrate.condition,
      "service_completed_successfully",
    );
    assert.equal(configuration.services.gateway.depends_on.api.condition, "service_healthy");
    assert.equal(configuration.services.gateway.depends_on.web.condition, "service_healthy");

    for (const serviceName of ["db", "api", "web", "gateway"]) {
      assert.ok(configuration.services[serviceName].healthcheck, `${serviceName} needs a health check`);
    }
  });

  test("clean exact runtime pins cover every gateway, PostgreSQL and OAuth consumer", () => {
    const local = renderCompose("local");
    const production = renderCompose("production");
    const tlsBootstrap = renderStandaloneCompose("compose.tls-bootstrap.yaml");

    assert.equal(local.services.gateway.image, nginxImage);
    assert.equal(tlsBootstrap.services.gateway.image, nginxImage);
    assert.equal(local.services.db.image, postgresImage);
    assert.equal(local.services.db.user, undefined);
    assert.equal(local.services.db.environment.PGDATA, "/var/lib/postgresql/data");
    assert.deepEqual(local.services.db.cap_add.sort(), [
      "CHOWN",
      "DAC_OVERRIDE",
      "FOWNER",
      "SETGID",
      "SETUID",
    ]);
    assert.deepEqual(local.services.db.healthcheck.test, [
      "CMD-SHELL",
      'pg_isready -U "$${POSTGRES_USER}" -d "$${POSTGRES_DB}"',
    ]);
    const databaseVolume = local.services.db.volumes.find(
      (volume) => volume.target === "/var/lib/postgresql/data",
    );
    assert.equal(databaseVolume?.source, "postgres-data");
    assert.equal(production.services.oauth2_proxy.image, oauthImage);
    assert.doesNotMatch(production.services.oauth2_proxy.image, /oauth2-proxy:latest@/);
    assert.ok(
      readProjectFile("apps/api/internal/application/integration_test.go").includes(
        `const postgresTestImage = ${JSON.stringify(postgresImage)}`,
      ),
    );
  });

  test("runtime services are constrained and logs are bounded", () => {
    const configuration = renderCompose("local");
    for (const serviceName of ["migrate", "api", "web", "gateway"]) {
      assertRuntimeHardening(serviceName, configuration.services[serviceName]);
    }
  });

  test("local mode exposes one configurable HTTP port without oauth2-proxy", () => {
    const configuration = renderCompose("local");
    assert.equal(configuration.services.oauth2_proxy, undefined);
    assert.equal(String(configuration.services.gateway.ports[0].published), "18080");
    assert.match(
      configuration.services.gateway.volumes
        .map((volume) => volume.source)
        .join("\n"),
      /local\.conf\.template$/,
    );
  });

  test("CI mode selects a non-overridable fixed owner identity", () => {
    const configuration = renderCompose("ci", {
      ...safeEnvironment,
      OWNER_GITHUB_LOGIN: "caller-selected-owner",
    });
    assert.equal(configuration.services.oauth2_proxy, undefined);
    assert.equal(configuration.services.api.environment.OWNER_GITHUB_LOGIN, "ci-owner");
    assert.match(
      configuration.services.gateway.volumes
        .map((volume) => volume.source)
        .join("\n"),
      /ci\.conf\.template$/,
    );
  });

  test("production uses immutable application images, TLS and the exact clean OAuth nightly", () => {
    const configuration = renderCompose("production");
    const gatewayPorts = configuration.services.gateway.ports.map((port) =>
      String(port.published),
    );

    assert.deepEqual(gatewayPorts, ["80", "443"]);
    const gatewayTLSMounts = configuration.services.gateway.volumes.filter((volume) =>
      volume.target.startsWith("/etc/nginx/tls"),
    );
    assert.deepEqual(gatewayTLSMounts.map((volume) => volume.target), ["/etc/nginx/tls"]);
    assert.equal(gatewayTLSMounts[0].read_only, true);
    assert.equal(configuration.services.oauth2_proxy.image, oauthImage);
    for (const serviceName of ["api", "migrate", "web"]) {
      assert.match(configuration.services[serviceName].image, /@sha256:[a-f0-9]{64}$/);
      assert.equal(configuration.services[serviceName].build, undefined);
      assert.equal(
        configuration.services[serviceName].depends_on.image_reference_preflight.condition,
        "service_completed_successfully",
      );
    }
    const imagePreflight = configuration.services.image_reference_preflight;
    assert.equal(imagePreflight.network_mode, "none");
    assert.equal(imagePreflight.read_only, true);
    assert.ok(imagePreflight.cap_drop.includes("ALL"));
    assert.match(imagePreflight.command.join("\n"), /@sha256:/);
    assert.match(imagePreflight.command.join("\n"), /\[!0-9a-f\]/);
    assert.equal(configuration.networks.auth.internal, true);
    const authNetworkMembers = Object.entries(configuration.services)
      .filter(([, service]) => Object.hasOwn(service.networks ?? {}, "auth"))
      .map(([name]) => name)
      .sort();
    assert.deepEqual(authNetworkMembers, ["gateway", "oauth2_proxy"]);
    assert.equal(configuration.services.oauth2_proxy.ports, undefined);
    assert.equal(configuration.services.oauth2_proxy.healthcheck, undefined);
    assert.equal(
      configuration.services.gateway.depends_on.oauth2_proxy.condition,
      "service_started",
    );
    assert.match(
      configuration.services.gateway.healthcheck.test.join(" "),
      /http:\/\/oauth2_proxy:4180\/ready/,
    );
  });

  test("production fails clearly when required deployment inputs are absent", () => {
    const environment = { ...safeEnvironment };
    for (const name of [
      "SERVER_NAME",
      "AUTH_SUBNET",
      "AUTH_GATEWAY_IP",
      "API_IMAGE",
      "MIGRATION_IMAGE",
      "WEB_IMAGE",
      "TLS_RUNTIME_DIRECTORY",
      "ACME_WEBROOT_PATH",
      "OAUTH2_PROXY_CLIENT_ID",
      "OAUTH2_PROXY_CLIENT_SECRET",
      "OAUTH2_PROXY_COOKIE_SECRET",
    ]) {
      environment[name] = "";
    }

    const result = spawnSync(
      "docker",
      [
        "compose",
        "--project-directory",
        projectRoot,
        "-f",
        projectFile("compose.yaml"),
        "-f",
        projectFile("compose.production.yaml"),
        "config",
      ],
      { cwd: projectRoot, env: environment, encoding: "utf8" },
    );

    assert.notEqual(result.status, 0);
    assert.match(`${result.stderr}\n${result.stdout}`, /required|must be set/i);
  });

  test("initial TLS bootstrap serves only HTTP ACME challenges through Nginx", () => {
    const configuration = renderStandaloneCompose("compose.tls-bootstrap.yaml");
    const runbook = readProjectFile("deploy/tls/README.md");
    assert.deepEqual(Object.keys(configuration.services), ["gateway"]);
    assert.deepEqual(
      configuration.services.gateway.ports.map((port) => String(port.published)),
      ["80"],
    );
    assert.ok(
      configuration.services.gateway.volumes.some((volume) =>
        volume.source.endsWith("tls-bootstrap.conf.template"),
      ),
    );
    assert.equal(configuration.services.gateway.read_only, true);
    assert.ok(configuration.services.gateway.cap_drop.includes("ALL"));
    assert.match(runbook, /TLS_RUNTIME_DIRECTORY/);
    assert.match(runbook, /install -o root -g 101 -m 0640/);
    assert.match(runbook, /nginx -t/);
    assert.match(runbook, /nginx -s reload/);
  });
});

describe("Container image policy", () => {
  test("API and migration images are reproducible CGO-free multi-stage builds", () => {
    const dockerfile = readProjectFile("apps/api/Dockerfile");
    const dockerignore = readProjectFile("apps/api/.dockerignore");
    const gooseToolModule = readProjectFile("apps/api/tools/goose/go.mod");
    const gooseToolSums = readProjectFile("apps/api/tools/goose/go.sum");

    assert.match(
      dockerfile,
      /^# syntax=docker\/dockerfile:1\.12@sha256:93bfd3b68c109427185cd78b4779fc82b484b0b7618e36d0f104d4d801e66d25$/m,
    );
    assert.doesNotMatch(dockerfile, /^ARG (?:GO|RUNTIME)_IMAGE=/m);
    assert.match(
      dockerfile,
      /^FROM golang:1\.26\.5-alpine3\.24@sha256:0178a641fbb4858c5f1b48e34bdaabe0350a330a1b1149aabd498d0699ff5fb2 AS dependencies$/m,
    );
    assert.doesNotMatch(dockerfile, /golang:1\.26\.3/);
    assert.equal(
      (dockerfile.match(/^FROM busybox:1\.37\.0-musl@sha256:[a-f0-9]{64} AS (?:migration|api)$/gm) ?? [])
        .length,
      2,
    );
    assert.match(dockerfile, /CGO_ENABLED=0/);
    assert.equal((dockerfile.match(/-mod=readonly/g) ?? []).length, 2);
    assert.doesNotMatch(dockerfile, /-mod=mod/);
    assert.match(dockerfile, /COPY tools\/goose\/go\.mod tools\/goose\/go\.sum \.\//);
    assert.match(gooseToolModule, /github\.com\/pressly\/goose\/v3 v3\.27\.3/);
    assert.match(gooseToolSums, /github\.com\/joho\/godotenv v1\.5\.1 h1:/);
    assert.match(gooseToolSums, /github\.com\/mfridman\/xflag v0\.1\.0 h1:/);
    assert.match(dockerfile, /USER 65532:65532/);
    assert.match(dockerfile, /HEALTHCHECK/);
    assert.match(dockerignore, /^\.env\.\*$/m);
    assert.match(dockerignore, /^\.git$/m);
  });

  test("web image uses npm ci and a non-root static-only final server", () => {
    const dockerfile = readProjectFile("apps/web/Dockerfile");
    const serverConfiguration = readProjectFile("apps/web/nginx.conf");

    assert.match(
      dockerfile,
      /^# syntax=docker\/dockerfile:1\.12@sha256:93bfd3b68c109427185cd78b4779fc82b484b0b7618e36d0f104d4d801e66d25$/m,
    );
    assert.doesNotMatch(dockerfile, /^ARG (?:NODE|NGINX)_IMAGE=/m);
    assert.match(dockerfile, /^FROM node:22\.12\.0-alpine3\.20@sha256:[a-f0-9]{64} AS build$/m);
    assert.ok(dockerfile.includes(`FROM ${nginxImage} AS web`));
    assert.match(dockerfile, /npm ci --ignore-scripts/);
    assert.match(dockerfile, /USER 101:101/);
    assert.match(dockerfile, /HEALTHCHECK/);
    assert.match(serverConfiguration, /try_files \$uri \$uri\/ \/index\.html/);
    assert.match(serverConfiguration, /max-age=31536000, immutable/);
    assert.match(serverConfiguration, /no-store/);
  });
});

describe("Gateway trust boundary", () => {
  const publicLocationMarkers = [
    "location = /healthz",
    "location = /readyz",
    "location ^~ /api/v1/expressions",
    "location = /expressions",
  ];

  test("public routes never invoke auth_request", () => {
    for (const mode of ["local", "ci", "production"]) {
      const configuration = readProjectFile(`deploy/nginx/${mode}.conf.template`);
      for (const marker of publicLocationMarkers) {
        assert.doesNotMatch(locationBlock(configuration, marker), /\bauth_request\b/);
      }
    }
  });

  test("local mode strips every caller-controlled owner and token header", () => {
    const configuration = readProjectFile("deploy/nginx/local.conf.template");
    const nginxConfiguration = readProjectFile("deploy/nginx/nginx.conf");
    const commonProxy = readProjectFile("deploy/nginx/snippets/api-proxy-common.conf");
    const ownerAPI = locationBlock(configuration, "location ^~ /api/v1/admin/");

    const logFormat = nginxConfiguration.match(/log_format main([\s\S]*?);/)?.[1] ?? "";
    assert.match(logFormat, /\$request_method/);
    assert.match(logFormat, /\$uri/);
    assert.doesNotMatch(logFormat, /\$(?:request|request_uri|args|query_string)(?![A-Za-z0-9_])/);
    assert.match(ownerAPI, /proxy_set_header X-Alright-Mate-Owner "";/);
    for (const header of [
      "X-Auth-Request-User",
      "X-Auth-Request-Email",
      "X-Auth-Request-Access-Token",
      "X-Forwarded-User",
      "X-Forwarded-Access-Token",
      "Authorization",
      "Cookie",
    ]) {
      assert.match(commonProxy, new RegExp(`proxy_set_header ${header} \\\"\\\";`));
    }
  });

  test("CI owner identity is fixed and cannot derive from any caller header", () => {
    const configuration = readProjectFile("deploy/nginx/ci.conf.template");
    const ownerAPI = locationBlock(configuration, "location ^~ /api/v1/admin/");

    assert.match(ownerAPI, /proxy_set_header X-Alright-Mate-Owner ci-owner;/);
    assert.doesNotMatch(ownerAPI, /\$http_/);
    assert.doesNotMatch(ownerAPI, /\$upstream_http_/);
    assert.doesNotMatch(ownerAPI, /\bauth_request\b/);
  });

  test("production protects owner paths and trusts only the auth response user", () => {
    const configuration = readProjectFile("deploy/nginx/production.conf.template");

    for (const marker of [
      "location ^~ /api/v1/admin/",
      "location = /admin",
      "location = /review",
    ]) {
      assert.match(locationBlock(configuration, marker), /auth_request \/oauth2\/auth;/);
    }

    const ownerAPI = locationBlock(configuration, "location ^~ /api/v1/admin/");
    assert.match(
      ownerAPI,
      /auth_request_set \$authenticated_owner \$upstream_http_x_auth_request_user;/,
    );
    assert.match(
      ownerAPI,
      /proxy_set_header X-Alright-Mate-Owner \$authenticated_owner;/,
    );
    assert.doesNotMatch(ownerAPI, /\$http_x_(?:auth|alright)/i);
    assert.doesNotMatch(ownerAPI, /Access-Token \$/);
  });

  test("oauth2-proxy has one public prefix and uses the conventional safe sign-in target", () => {
    const configuration = readProjectFile("deploy/nginx/production.conf.template");
    const authEndpoint = locationBlock(configuration, "location = /oauth2/auth");
    const publicOAuth = locationBlock(configuration, "location ^~ /oauth2/");
    const signIn = locationBlock(configuration, "location @oauth2_signin");

    assert.match(authEndpoint, /\binternal;/);
    assert.match(publicOAuth, /proxy_pass http:\/\/oauth2_proxy:4180;/);
    assert.match(signIn, /\/oauth2\/start\?rd=\/admin/);
  });

  test("metrics is explicitly denied and never proxied by any gateway mode", () => {
    for (const mode of ["local", "ci", "production"]) {
      const configuration = readProjectFile(`deploy/nginx/${mode}.conf.template`);
      const metrics = locationBlock(configuration, "location = /metrics");
      assert.match(metrics, /return 404;/);
      assert.doesNotMatch(metrics, /proxy_pass/);
    }
  });
});

test("oauth2-proxy is configured for one GitHub owner without token forwarding", () => {
  const oauthConfiguration = readProjectFile("deploy/oauth2-proxy/oauth2-proxy.cfg");
  const production = renderCompose("production");
  const command = production.services.oauth2_proxy.command.join(" ");

  assert.match(oauthConfiguration, /provider = "github"/);
  assert.match(oauthConfiguration, /email_domains = \["\*"\]/);
  assert.match(oauthConfiguration, /set_xauthrequest = true/);
  assert.match(oauthConfiguration, /reverse_proxy = true/);
  assert.match(oauthConfiguration, /cookie_secure = true/);
  assert.match(oauthConfiguration, /cookie_httponly = true/);
  assert.match(oauthConfiguration, /cookie_samesite = "lax"/);
  assert.match(command, /--github-user=test-owner/);
  assert.doesNotMatch(command, /--trusted-(?:proxy-)?ip/);
  assert.doesNotMatch(command, /pass-access-token|set-authorization-header/);
});
