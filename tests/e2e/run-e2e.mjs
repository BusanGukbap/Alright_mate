import { randomBytes } from "node:crypto";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { spawnSync } from "node:child_process";

const e2eDirectory = path.dirname(fileURLToPath(import.meta.url));
const repositoryRoot = path.resolve(e2eDirectory, "../..");
const runSuffix = randomBytes(4).toString("hex");
const projectName = `alright-mate-task4b-${process.pid}-${runSuffix}`;
const httpPort = process.env.E2E_HTTP_PORT ?? "18142";
const baseUrl = `http://127.0.0.1:${httpPort}`;
const composeFiles = ["-f", "compose.yaml", "-f", "compose.ci.yaml"];
const composeEnvironment = {
  ...process.env,
  APP_HTTP_PORT: httpPort,
  IMAGE_TAG: `task4b-${process.pid}-${runSuffix}`,
  POSTGRES_DB: "alrightmate_e2e",
  POSTGRES_USER: "alrightmate_e2e",
  POSTGRES_PASSWORD: "local-e2e-only",
  DATABASE_URL:
    "postgres://alrightmate_e2e:local-e2e-only@db:5432/alrightmate_e2e?sslmode=disable",
};

if (!/^alright-mate-task4b-\d+-[0-9a-f]{8}$/.test(projectName)) {
  throw new Error("Refusing to use an unexpected Compose project name.");
}
if (!/^\d{2,5}$/.test(httpPort)) {
  throw new Error("E2E_HTTP_PORT must be a numeric TCP port.");
}

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: repositoryRoot,
    env: composeEnvironment,
    encoding: "utf8",
    stdio: "inherit",
    ...options,
  });
  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    throw new Error(`${command} exited with status ${result.status ?? "unknown"}.`);
  }
  return result;
}

function compose(...args) {
  run("docker", ["compose", "-p", projectName, ...composeFiles, ...args]);
}

function assertProjectIsUnused() {
  const resourceCommands = [
    ["container", "ls", "--all"],
    ["network", "ls"],
    ["volume", "ls"],
  ];
  const staleResources = resourceCommands.flatMap(([resource, ...args]) => {
    const result = run(
      "docker",
      [
        resource,
        ...args,
        "--quiet",
        "--filter",
        `label=com.docker.compose.project=${projectName}`,
      ],
      { stdio: ["ignore", "pipe", "inherit"] },
    );
    const ids = result.stdout.trim().split(/\s+/).filter(Boolean);
    return ids.map((id) => `${resource}:${id}`);
  });
  if (staleResources.length > 0) {
    throw new Error(
      `Refusing to reuse a stale Compose project (${projectName}): ${staleResources.join(", ")}`,
    );
  }
}

async function waitForReadiness() {
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${baseUrl}/readyz`, { signal: AbortSignal.timeout(2_000) });
      if (response.ok) {
        return;
      }
    } catch {
      // The gateway may still be starting; the bounded readiness loop will retry.
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`The CI gateway did not become ready at ${baseUrl}/readyz.`);
}

let exitCode = 1;
let ownsProject = false;
try {
  assertProjectIsUnused();
  ownsProject = true;
  console.log(`Task 4B Compose project: ${projectName}`);
  compose("config", "--quiet");
  compose("up", "-d", "--build", "--wait", "--wait-timeout", "180");
  await waitForReadiness();

  const playwrightCli = path.join(e2eDirectory, "node_modules", "@playwright", "test", "cli.js");
  const result = spawnSync(
    process.execPath,
    [playwrightCli, "test", "--config=playwright.config.ts", ...process.argv.slice(2)],
    {
      cwd: e2eDirectory,
      env: {
        ...process.env,
        E2E_BASE_URL: baseUrl,
        E2E_COMPOSE_PROJECT_NAME: projectName,
        E2E_POSTGRES_DB: composeEnvironment.POSTGRES_DB,
        E2E_POSTGRES_USER: composeEnvironment.POSTGRES_USER,
        E2E_REPOSITORY_ROOT: repositoryRoot,
      },
      encoding: "utf8",
      stdio: "inherit",
    },
  );
  if (result.error) {
    throw result.error;
  }
  exitCode = result.status ?? 1;
} catch (error) {
  console.error(error instanceof Error ? error.message : error);
} finally {
  if (ownsProject) {
    try {
      compose("down", "--volumes", "--remove-orphans", "--timeout", "20");
    } catch (cleanupError) {
      console.error("The disposable Task 4B stack could not be cleaned up.", cleanupError);
      exitCode = 1;
    }
  }
}

process.exitCode = exitCode;
