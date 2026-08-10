package application

import (
	"context"
	"database/sql"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	_ "github.com/jackc/pgx/v5/stdlib"
	"github.com/pressly/goose/v3"
	"github.com/testcontainers/testcontainers-go"
	"github.com/testcontainers/testcontainers-go/wait"
)

const postgresTestImage = "cgr.dev/chainguard/postgres:latest@sha256:e67e686bf76586ff0b87fd42553f2dce5e461cb76e090f502ab129aaef141f12"

var (
	integrationPool        *pgxpool.Pool
	integrationUnavailable error
)

func TestMain(m *testing.M) {
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
	defer cancel()

	request := testcontainers.ContainerRequest{
		Image:        postgresTestImage,
		ExposedPorts: []string{"5432/tcp"},
		Env: map[string]string{
			"POSTGRES_DB":       "alrightmate",
			"POSTGRES_USER":     "postgres",
			"POSTGRES_PASSWORD": "postgres",
		},
		WaitingFor: wait.ForListeningPort("5432/tcp").WithStartupTimeout(2 * time.Minute),
	}

	container, err := testcontainers.GenericContainer(ctx, testcontainers.GenericContainerRequest{
		ContainerRequest: request,
		Started:          true,
	})
	if err != nil {
		integrationUnavailable = fmt.Errorf("start PostgreSQL test container: %w", err)
		if os.Getenv("REQUIRE_DOCKER") == "1" {
			fmt.Fprintln(os.Stderr, integrationUnavailable)
			os.Exit(1)
		}
		os.Exit(m.Run())
	}
	if err := prepareIntegrationDatabase(ctx, container); err != nil {
		fmt.Fprintf(os.Stderr, "prepare PostgreSQL integration database: %v\n", err)
		_ = container.Terminate(context.Background())
		os.Exit(1)
	}

	code := m.Run()
	integrationPool.Close()
	if err := container.Terminate(context.Background()); err != nil && code == 0 {
		fmt.Fprintf(os.Stderr, "terminate PostgreSQL test container: %v\n", err)
		code = 1
	}
	os.Exit(code)
}

func prepareIntegrationDatabase(ctx context.Context, container testcontainers.Container) error {
	host, err := container.Host(ctx)
	if err != nil {
		return fmt.Errorf("get container host: %w", err)
	}
	port, err := container.MappedPort(ctx, "5432/tcp")
	if err != nil {
		return fmt.Errorf("get PostgreSQL port: %w", err)
	}
	dsn := fmt.Sprintf("postgres://postgres:postgres@%s/alrightmate?sslmode=disable", net.JoinHostPort(host, port.Port()))

	database, err := sql.Open("pgx", dsn)
	if err != nil {
		return fmt.Errorf("open Goose database: %w", err)
	}
	defer database.Close()
	if err := database.PingContext(ctx); err != nil {
		return fmt.Errorf("ping Goose database: %w", err)
	}
	if err := goose.SetDialect("postgres"); err != nil {
		return fmt.Errorf("set Goose dialect: %w", err)
	}
	migrations, err := filepath.Abs(filepath.Join("..", "..", "db", "migrations"))
	if err != nil {
		return fmt.Errorf("resolve migrations directory: %w", err)
	}
	if err := goose.UpContext(ctx, database, migrations); err != nil {
		return fmt.Errorf("apply Goose migrations: %w", err)
	}

	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		return fmt.Errorf("open pgx pool: %w", err)
	}
	if err := pool.Ping(ctx); err != nil {
		pool.Close()
		return fmt.Errorf("ping pgx pool: %w", err)
	}
	integrationPool = pool
	return nil
}

func requireIntegration(t *testing.T) *pgxpool.Pool {
	t.Helper()
	if integrationUnavailable != nil {
		t.Skipf("PostgreSQL integration test skipped: %v", integrationUnavailable)
	}
	if integrationPool == nil {
		t.Fatal("PostgreSQL integration pool is nil")
	}
	if _, err := integrationPool.Exec(t.Context(), `
		TRUNCATE review_events, expression_review_state, expression_tags, expressions
	`); err != nil {
		t.Fatalf("reset PostgreSQL integration database: %v", err)
	}
	return integrationPool
}

func installTemporaryDatabaseObjects(t *testing.T, setupSQL, cleanupSQL string) func() {
	t.Helper()
	if _, err := integrationPool.Exec(t.Context(), setupSQL); err != nil {
		t.Fatalf("install temporary PostgreSQL test objects: %v", err)
	}
	cleanup := func() {
		t.Helper()
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if _, err := integrationPool.Exec(ctx, cleanupSQL); err != nil {
			t.Errorf("clean temporary PostgreSQL test objects: %v", err)
		}
	}
	t.Cleanup(cleanup)
	return cleanup
}

type fixedClock struct {
	now time.Time
}

func (clock *fixedClock) Now() time.Time {
	return clock.now
}

func (clock *fixedClock) Set(now time.Time) {
	clock.now = now
}

func stringPointer(value string) *string {
	return &value
}
