import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { spawnSync } from "node:child_process";

import { expect, test as base } from "@playwright/test";

const e2eDirectory = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const seed = readFileSync(path.join(e2eDirectory, "fixtures", "seed.sql"), "utf8");

function requiredEnvironment(name: string) {
  const value = process.env[name]?.trim();
  if (!value) {
    throw new Error(`${name} is required for the real-stack database reset.`);
  }
  return value;
}

function resetDatabase() {
  const repositoryRoot = requiredEnvironment("E2E_REPOSITORY_ROOT");
  const projectName = requiredEnvironment("E2E_COMPOSE_PROJECT_NAME");
  const postgresUser = requiredEnvironment("E2E_POSTGRES_USER");
  const postgresDatabase = requiredEnvironment("E2E_POSTGRES_DB");
  const result = spawnSync(
    "docker",
    [
      "compose",
      "-p",
      projectName,
      "-f",
      "compose.yaml",
      "-f",
      "compose.ci.yaml",
      "exec",
      "-T",
      "db",
      "psql",
      "-v",
      "ON_ERROR_STOP=1",
      "-U",
      postgresUser,
      "-d",
      postgresDatabase,
    ],
    {
      cwd: repositoryRoot,
      env: process.env,
      encoding: "utf8",
      input: seed,
      stdio: ["pipe", "inherit", "inherit"],
    },
  );
  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    throw new Error(`Database reset exited with status ${result.status ?? "unknown"}.`);
  }
}

export const test = base.extend<{ databaseReset: void }>({
  databaseReset: [
    async ({}, use) => {
      resetDatabase();
      await use();
    },
    { auto: true },
  ],
});

export { expect };
