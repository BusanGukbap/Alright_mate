import assert from "node:assert/strict";
import { after, test } from "node:test";
import { copyFileSync, mkdtempSync, rmSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { connect as tlsConnect } from "node:tls";
import { fileURLToPath } from "node:url";

const enabled = process.env.RUNTIME_DOCKER_TESTS === "1";
const testDirectory = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(testDirectory, "..", "..");
const nginxImage =
  "nginx:1.30.4-alpine3.24-slim@sha256:ddde39c6e51f02fde7410c2e9c234cf2d0a4c7bdbbe176aeb37d8ad7ab4eb58c";
const oauthImage =
  "quay.io/oauth2-proxy/oauth2-proxy-nightly:latest@sha256:522b5685d761526dcae84cc0bef0037475749608a3564a2c9314db563cd8f099";
const apiImage = "alright-mate-api:task-4a-test";
const migrationImage = "alright-mate-migration:task-4a-test";
const webImage = "alright-mate-web:task-4a-test";
let tlsDirectory;

after(() => {
  if (tlsDirectory) rmSync(tlsDirectory, { recursive: true, force: true });
});

function dockerResult(arguments_, options = {}) {
  return spawnSync("docker", arguments_, {
    cwd: projectRoot,
    env: { ...process.env, ...options.env },
    encoding: "utf8",
    maxBuffer: 20 * 1024 * 1024,
    timeout: options.timeout ?? 10 * 60 * 1000,
  });
}

function docker(arguments_, options = {}) {
  const result = dockerResult(arguments_, options);
  assert.equal(
    result.status,
    0,
    `docker ${arguments_.join(" ")} failed:\n${result.stderr || result.stdout}`,
  );
  return result.stdout;
}

function bindMount(source, target) {
  return `type=bind,source=${path.resolve(source)},target=${target},readonly`;
}

function generateTLSCertificate(directory) {
  const openssl = process.env.OPENSSL_BIN ?? "openssl";
  const result = spawnSync(
    openssl,
    [
      "req",
      "-x509",
      "-newkey",
      "rsa:2048",
      "-sha256",
      "-nodes",
      "-days",
      "2",
      "-subj",
      "/CN=example.invalid",
      "-addext",
      "subjectAltName=DNS:example.invalid",
      "-keyout",
      path.join(directory, "privkey.pem"),
      "-out",
      path.join(directory, "fullchain.pem"),
    ],
    { cwd: projectRoot, encoding: "utf8" },
  );
  assert.equal(
    result.status,
    0,
    `OpenSSL test-certificate generation failed:\n${result.stderr || result.stdout}`,
  );
}

function ensureTLSFixture() {
  if (tlsDirectory) return tlsDirectory;
  tlsDirectory = mkdtempSync(path.join(tmpdir(), "alright-mate-task4a-tls-"));
  generateTLSCertificate(tlsDirectory);
  return tlsDirectory;
}

async function freePort() {
  const server = createServer();
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  assert.equal(typeof address, "object");
  const port = address.port;
  await new Promise((resolve, reject) => server.close((error) => (error ? reject(error) : resolve())));
  return port;
}

function readTLSFingerprint(port) {
  return new Promise((resolve, reject) => {
    const socket = tlsConnect(
      {
        host: "127.0.0.1",
        port,
        servername: "example.invalid",
        rejectUnauthorized: false,
      },
      () => {
        const fingerprint = socket.getPeerCertificate().fingerprint256;
        socket.destroy();
        resolve(fingerprint);
      },
    );
    socket.setTimeout(1_000, () => socket.destroy(new Error("TLS handshake timed out")));
    socket.once("error", reject);
  });
}

async function waitForTLSFingerprint(port, previousFingerprint) {
  let lastError;
  for (let attempt = 0; attempt < 40; attempt += 1) {
    try {
      const fingerprint = await readTLSFingerprint(port);
      if (fingerprint && fingerprint !== previousFingerprint) return fingerprint;
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw lastError ?? new Error("Nginx did not present the renewed TLS certificate");
}

function nginxSyntax(mode) {
  const arguments_ = [
    "run",
    "--rm",
    "--read-only",
    "--tmpfs",
    "/etc/nginx/conf.d:rw,mode=0755",
    "--tmpfs",
    "/tmp:rw,mode=1777",
    "--add-host",
    "api:127.0.0.1",
    "--add-host",
    "web:127.0.0.1",
    "--add-host",
    "oauth2_proxy:127.0.0.1",
    "--env",
    "SERVER_NAME=example.invalid",
    "--env",
    "NGINX_ENVSUBST_FILTER=^SERVER_NAME$",
    "--mount",
    bindMount(path.join(projectRoot, "deploy", "nginx", "nginx.conf"), "/etc/nginx/nginx.conf"),
    "--mount",
    bindMount(path.join(projectRoot, "deploy", "nginx", "snippets"), "/etc/nginx/snippets"),
    "--mount",
    bindMount(
      path.join(projectRoot, "deploy", "nginx", `${mode}.conf.template`),
      "/etc/nginx/templates/default.conf.template",
    ),
  ];

  if (mode === "production") {
    const fixtureDirectory = ensureTLSFixture();
    arguments_.push(
      "--mount",
      bindMount(fixtureDirectory, "/etc/nginx/tls"),
      "--mount",
      bindMount(testDirectory, "/var/www/certbot"),
    );
  }
  arguments_.push(nginxImage, "nginx", "-t");
  docker(arguments_);
}

function composeArguments(project, mode, ...command) {
  return [
    "compose",
    "-p",
    project,
    "--project-directory",
    projectRoot,
    "-f",
    path.join(projectRoot, "compose.yaml"),
    "-f",
    path.join(projectRoot, `compose.${mode}.yaml`),
    ...command,
  ];
}

function smokeEnvironment(port, suffix) {
  return {
    APP_HTTP_PORT: String(port),
    POSTGRES_DB: "alrightmate",
    POSTGRES_USER: "alrightmate",
    POSTGRES_PASSWORD: `non-secret-${suffix}`,
    DATABASE_URL: `postgres://alrightmate:non-secret-${suffix}@db:5432/alrightmate?sslmode=disable`,
    OWNER_GITHUB_LOGIN: "local-owner",
    BUILD_VERSION: "task-4a-test",
    IMAGE_TAG: "task-4a-test",
    API_IMAGE: apiImage,
    MIGRATION_IMAGE: migrationImage,
    WEB_IMAGE: webImage,
  };
}

function productionEnvironment(overrides = {}) {
  return {
    ...smokeEnvironment(18443, "production-preflight"),
    SERVER_NAME: "example.invalid",
    AUTH_SUBNET: "172.30.0.0/24",
    AUTH_GATEWAY_IP: "172.30.0.10",
    API_IMAGE:
      "registry.invalid/alright-mate-api@sha256:1111111111111111111111111111111111111111111111111111111111111111",
    MIGRATION_IMAGE:
      "registry.invalid/alright-mate-migration@sha256:2222222222222222222222222222222222222222222222222222222222222222",
    WEB_IMAGE:
      "registry.invalid/alright-mate-web@sha256:3333333333333333333333333333333333333333333333333333333333333333",
    TLS_RUNTIME_DIRECTORY: ensureTLSFixture(),
    ACME_WEBROOT_PATH: testDirectory,
    OAUTH2_PROXY_CLIENT_ID: "dummy-client-id",
    OAUTH2_PROXY_CLIENT_SECRET: "dummy-client-secret",
    OAUTH2_PROXY_COOKIE_SECRET: "QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUE=",
    ...overrides,
  };
}

test("pinned Nginx parses all modes and reloads a renewed directory certificate", { skip: !enabled }, async () => {
  for (const mode of ["local", "ci", "production"]) nginxSyntax(mode);

  const fixtureDirectory = ensureTLSFixture();
  const renewedDirectory = mkdtempSync(path.join(tmpdir(), "alright-mate-task4a-renewed-tls-"));
  const container = `alright-mate-task4a-tls-${process.pid}`;
  const port = await freePort();
  let started = false;

  try {
    docker([
      "run",
      "--detach",
      "--name",
      container,
      "--user",
      "101:101",
      "--read-only",
      "--cap-drop",
      "ALL",
      "--security-opt",
      "no-new-privileges=true",
      "--tmpfs",
      "/etc/nginx/conf.d:rw,uid=101,gid=101,mode=0755",
      "--tmpfs",
      "/tmp:rw,uid=101,gid=101,mode=1777",
      "--add-host",
      "api:127.0.0.1",
      "--add-host",
      "web:127.0.0.1",
      "--add-host",
      "oauth2_proxy:127.0.0.1",
      "--env",
      "SERVER_NAME=example.invalid",
      "--env",
      "NGINX_ENVSUBST_FILTER=^SERVER_NAME$",
      "--publish",
      `127.0.0.1:${port}:8443`,
      "--mount",
      bindMount(path.join(projectRoot, "deploy", "nginx", "nginx.conf"), "/etc/nginx/nginx.conf"),
      "--mount",
      bindMount(path.join(projectRoot, "deploy", "nginx", "snippets"), "/etc/nginx/snippets"),
      "--mount",
      bindMount(
        path.join(projectRoot, "deploy", "nginx", "production.conf.template"),
        "/etc/nginx/templates/default.conf.template",
      ),
      "--mount",
      bindMount(fixtureDirectory, "/etc/nginx/tls"),
      "--mount",
      bindMount(testDirectory, "/var/www/certbot"),
      nginxImage,
    ]);
    started = true;

    const initialFingerprint = await waitForTLSFingerprint(port);
    generateTLSCertificate(renewedDirectory);
    copyFileSync(path.join(renewedDirectory, "privkey.pem"), path.join(fixtureDirectory, "privkey.pem"));
    copyFileSync(
      path.join(renewedDirectory, "fullchain.pem"),
      path.join(fixtureDirectory, "fullchain.pem"),
    );
    docker(["exec", container, "nginx", "-t"]);
    docker(["exec", container, "nginx", "-s", "reload"]);
    const renewedFingerprint = await waitForTLSFingerprint(port, initialFingerprint);
    assert.notEqual(renewedFingerprint, initialFingerprint);
  } finally {
    if (started) dockerResult(["rm", "--force", container]);
    rmSync(renewedDirectory, { recursive: true, force: true });
  }
});

test("production rejects mutable images and probes oauth2-proxy listener readiness", { skip: !enabled }, async () => {
  const project = `alright-mate-task4a-production-preflight-${process.pid}`;
  const environment = productionEnvironment();
  try {
    docker(composeArguments(project, "production", "run", "--rm", "--no-deps", "image_reference_preflight"), {
      env: environment,
    });
    const mutableResult = dockerResult(
      composeArguments(project, "production", "run", "--rm", "--no-deps", "image_reference_preflight"),
      { env: { ...environment, API_IMAGE: "registry.invalid/alright-mate-api:latest" } },
    );
    assert.notEqual(mutableResult.status, 0);
    assert.match(`${mutableResult.stderr}\n${mutableResult.stdout}`, /must use name@sha256/);
  } finally {
    dockerResult(composeArguments(project, "production", "down", "--volumes", "--remove-orphans"), {
      env: environment,
    });
  }

  docker([
    "run",
    "--rm",
    "--read-only",
    "--tmpfs",
    "/tmp:rw,mode=1777",
    "--env",
    "OAUTH2_PROXY_CLIENT_ID=dummy-client-id",
    "--env",
    "OAUTH2_PROXY_CLIENT_SECRET=dummy-client-secret",
    "--env",
    "OAUTH2_PROXY_COOKIE_SECRET=QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUE=",
    "--mount",
    bindMount(
      path.join(projectRoot, "deploy", "oauth2-proxy", "oauth2-proxy.cfg"),
      "/etc/oauth2-proxy.cfg",
    ),
    oauthImage,
    "--config=/etc/oauth2-proxy.cfg",
    "--github-user=test-owner",
    "--redirect-url=https://example.invalid/oauth2/callback",
    "--config-test",
  ]);

  const network = `alright-mate-task4a-auth-${process.pid}`;
  const container = `alright-mate-task4a-oauth-${process.pid}`;
  let started = false;
  docker(["network", "create", "--internal", network]);
  try {
    docker([
      "run",
      "--detach",
      "--name",
      container,
      "--network",
      network,
      "--network-alias",
      "oauth2_proxy",
      "--read-only",
      "--cap-drop",
      "ALL",
      "--security-opt",
      "no-new-privileges=true",
      "--tmpfs",
      "/tmp:rw,mode=1777",
      "--env",
      "OAUTH2_PROXY_CLIENT_ID=dummy-client-id",
      "--env",
      "OAUTH2_PROXY_CLIENT_SECRET=dummy-client-secret",
      "--env",
      "OAUTH2_PROXY_COOKIE_SECRET=QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUE=",
      "--mount",
      bindMount(
        path.join(projectRoot, "deploy", "oauth2-proxy", "oauth2-proxy.cfg"),
        "/etc/oauth2-proxy.cfg",
      ),
      oauthImage,
      "--config=/etc/oauth2-proxy.cfg",
      "--github-user=test-owner",
      "--redirect-url=https://example.invalid/oauth2/callback",
    ]);
    started = true;

    let ready = false;
    for (let attempt = 0; attempt < 30 && !ready; attempt += 1) {
      const probe = dockerResult([
        "run",
        "--rm",
        "--network",
        network,
        nginxImage,
        "wget",
        "-q",
        "-O",
        "/dev/null",
        "http://oauth2_proxy:4180/ready",
      ]);
      ready = probe.status === 0;
      if (!ready) await new Promise((resolve) => setTimeout(resolve, 100));
    }
    assert.equal(ready, true, dockerResult(["logs", container]).stdout);
  } finally {
    if (started) dockerResult(["rm", "--force", container]);
    dockerResult(["network", "rm", network]);
  }
});

test("application images build with minimal non-root runtime metadata", { skip: !enabled }, () => {
  docker([
    "build",
    "--target",
    "api",
    "--build-arg",
    "BUILD_VERSION=task-4a-test",
    "--tag",
    apiImage,
    path.join(projectRoot, "apps", "api"),
  ]);
  docker([
    "build",
    "--target",
    "migration",
    "--build-arg",
    "BUILD_VERSION=task-4a-test",
    "--tag",
    migrationImage,
    path.join(projectRoot, "apps", "api"),
  ]);
  docker([
    "build",
    "--build-arg",
    "BUILD_VERSION=task-4a-test",
    "--tag",
    webImage,
    path.join(projectRoot, "apps", "web"),
  ]);

  for (const [image, expectedUser] of [
    [apiImage, "65532:65532"],
    [migrationImage, "65532:65532"],
    [webImage, "101:101"],
  ]) {
    const [inspection] = JSON.parse(docker(["image", "inspect", image]));
    assert.equal(inspection.Config.User, expectedUser);
    if (image !== migrationImage) assert.ok(inspection.Config.Healthcheck);
  }

  docker(["run", "--rm", "--entrypoint", "sh", apiImage, "-c", "test ! -e /usr/local/go/bin/go && test ! -e /app/go.mod"]);
  docker(["run", "--rm", "--entrypoint", "sh", webImage, "-c", "test ! -e /app/package.json && test ! -e /usr/local/bin/node"]);
});

for (const mode of ["local", "ci"]) {
  test(`${mode} stack proves migration readiness and the owner-header boundary`, { skip: !enabled }, async () => {
    const project = `alright-mate-task4a-${mode}-${process.pid}`;
    const port = await freePort();
    const environment = smokeEnvironment(port, `${mode}-${process.pid}`);

    try {
      docker(composeArguments(project, mode, "up", "--detach", "--no-build", "--wait", "--wait-timeout", "180"), {
        env: environment,
      });

      const databaseContainer = docker(
        composeArguments(project, mode, "ps", "--quiet", "db"),
        { env: environment },
      ).trim();
      const [databaseInspection] = JSON.parse(docker(["inspect", databaseContainer]));
      assert.equal(
        Object.hasOwn(databaseInspection.HostConfig.PortBindings ?? {}, "5432/tcp"),
        false,
      );
      const databaseMount = databaseInspection.Mounts.find(
        (mount) => mount.Destination === "/var/lib/postgresql/data",
      );
      assert.equal(databaseMount?.Type, "volume");
      assert.equal(databaseMount?.Name, `${project}_postgres-data`);
      const databaseProcesses = docker(["top", databaseContainer, "-eo", "pid,uid,comm"])
        .trim()
        .split(/\r?\n/)
        .slice(1)
        .filter((line) => /\bpostgres\s*$/.test(line));
      assert.ok(databaseProcesses.length > 0, "PostgreSQL must have a steady-state process");
      for (const processLine of databaseProcesses) {
        assert.match(processLine, /^\s*\d+\s+70\s+postgres\s*$/);
      }

      const root = await fetch(`http://127.0.0.1:${port}/`);
      assert.equal(root.status, 200);
      const health = await fetch(`http://127.0.0.1:${port}/healthz`);
      assert.equal(health.status, 200);
      const readiness = await fetch(`http://127.0.0.1:${port}/readyz`);
      assert.equal(readiness.status, 200);
      const publicAPI = await fetch(`http://127.0.0.1:${port}/api/v1/expressions`);
      assert.equal(publicAPI.status, 200);
      const directAPI = dockerResult(
        composeArguments(
          project,
          mode,
          "exec",
          "-T",
          "web",
          "wget",
          "-q",
          "-O",
          "/dev/null",
          "http://api:8080/healthz",
        ),
        { env: environment },
      );
      assert.notEqual(directAPI.status, 0, "the web container must not resolve or reach the API");
      const metrics = await fetch(`http://127.0.0.1:${port}/metrics`);
      assert.equal(metrics.status, 404);

      const oauthCallback = await fetch(
        `http://127.0.0.1:${port}/oauth2/callback?code=oauth-code-canary&state=oauth-state-canary`,
      );
      assert.equal(oauthCallback.status, 404);
      const gatewayLogs = docker(composeArguments(project, mode, "logs", "--no-color", "gateway"), {
        env: environment,
      });
      assert.doesNotMatch(gatewayLogs, /oauth-(?:code|state)-canary/);

      const ownerAPI = await fetch(`http://127.0.0.1:${port}/api/v1/admin/reviews/due`, {
        headers: { "X-Alright-Mate-Owner": mode === "local" ? "local-owner" : "attacker" },
      });
      assert.equal(ownerAPI.status, mode === "local" ? 401 : 200);
    } finally {
      docker(composeArguments(project, mode, "down", "--volumes", "--remove-orphans"), {
        env: environment,
      });
    }
  });
}
