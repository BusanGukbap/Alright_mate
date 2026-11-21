# Alright, Mate? project instructions

## Resume work

- Use the branch and task specified by the user. For the operations labs, start from `codex/ops-learning-baseline` and follow [README.md](README.md) to run the app.
- Read the relevant [GitHub Issue](https://github.com/BusanGukbap/Alright_mate/issues) for the goal, last verified commit and next step. Check `git status --short --branch` before editing and preserve local work.

## Run and verify

- The Go server embeds the HTML and JavaScript in `web/`. PostgreSQL uses `schema.sql`. Follow the README for setup and keep database connection strings and credentials out of Git, Issues and logs.
- Use `go test ./...` for Go changes. For database behaviour, apply the schema to a disposable database and set `TEST_DATABASE_URL`. The persistence test skips without it. Check browser behaviour when changing the UI.
- Use only an isolated PostgreSQL instance for fault drills. Check `/livez`, `/readyz` and the `X-Request-ID` response header against the JSON logs.

## Record and publish

- In the Issue or PR, record the consequential AI contribution, the user's decisions or approvals, what was actually verified, and the next step. Link the relevant commit or check. Leave out full chat transcripts, secrets and local database contents.
- Keep UI copy and repository docs in British English. Ask for explicit approval before committing the current diff, pushing the named branch, or creating, editing or merging a PR.
