# Alright, Mate?

A small web app for saving British English expressions. The browser and JSON API share one Go server. PostgreSQL stores the expressions across server restarts.

## Run locally on Windows

You need Go 1.26 or later, PostgreSQL and PowerShell. Open PowerShell in this folder.

1. Create a local PostgreSQL database named `alright_mate`. For example, use pgAdmin or run `createdb -U postgres alright_mate`. Make sure the PostgreSQL service is running.
2. Apply the schema and start the app. Replace `USERNAME` and `PASSWORD` with your local values and URL-encode special characters in them. If `psql` is outside your PATH, run it from your PostgreSQL installation's `bin` directory by full path.

   ```powershell
   $env:DATABASE_URL = 'postgres://USERNAME:PASSWORD@127.0.0.1:5432/alright_mate?sslmode=disable'
   psql --dbname=$env:DATABASE_URL -v ON_ERROR_STOP=1 -f .\schema.sql
   go run .
   ```

3. Open [http://127.0.0.1:8080](http://127.0.0.1:8080), add an expression such as “Chuffed to bits” with the meaning “Very pleased”, then restart Go and reload the page.

The app listens on `127.0.0.1:8080` by default. Use the `ADDR` environment variable to change the listening address. Keep the app on a private interface or add access controls before exposing its write endpoint. For installation-specific TLS settings, use the PostgreSQL connection options appropriate to your environment.

## Start the operations exercises

| Check | What to look for |
| --- | --- |
| `GET /livez` | HTTP 200 while the HTTP process responds. |
| `GET /readyz` | HTTP 200 once PostgreSQL and the schema are available. HTTP 503 if either is unavailable. |
| Request log | One JSON line on standard output with the method, path, status, duration and request ID. Find the same ID in the browser's `X-Request-ID` response header. |

With a **disposable** PostgreSQL instance, stop only that instance and check both endpoints. Try adding an expression and inspect the 503 and related database log. Restart the instance and verify that readiness returns to 200 and existing expressions are visible.

Run `go test ./...` for HTTP input validation. To include the real PostgreSQL persistence test, create a dedicated test database, apply `schema.sql`, set `TEST_DATABASE_URL` to its connection URL and run `go test -v ./...`. Run `go build ./...` to build the service. The binary embeds the browser files.
