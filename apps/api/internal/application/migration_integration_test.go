package application

import (
	"errors"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgconn"
)

func TestInitialMigrationAppliesAndEnforcesContractedData(t *testing.T) {
	pool := requireIntegration(t)
	ctx := t.Context()

	var version int64
	if err := pool.QueryRow(ctx, `
		SELECT version_id
		FROM goose_db_version
		WHERE is_applied
		ORDER BY id DESC
		LIMIT 1
	`).Scan(&version); err != nil {
		t.Fatalf("read applied Goose migration version: %v", err)
	}
	if version != 1 {
		t.Fatalf("applied Goose migration version = %d, want 1", version)
	}

	now := time.Date(2026, time.August, 8, 4, 0, 0, 0, time.UTC)
	assertCheckViolation(t, `
		INSERT INTO expressions (expression, meaning_ko, status, created_at, updated_at, published_at)
		VALUES ('invalid lifecycle', '잘못된 상태', 'draft', $1, $1, $1)
	`, now)
	assertCheckViolation(t, `
		INSERT INTO expressions (expression, meaning_ko, source_url, status, created_at, updated_at)
		VALUES ('invalid source', '잘못된 출처', 'ftp://example.com/source', 'draft', $1, $1)
	`, now)

	var expressionID uuid.UUID
	if err := pool.QueryRow(ctx, `
		INSERT INTO expressions (
			expression, meaning_ko, source_url, status,
			created_at, updated_at, published_at
		)
		VALUES ('valid row', '유효한 행', 'https://example.com/source', 'published', $1, $1, $1)
		RETURNING id
	`, now).Scan(&expressionID); err != nil {
		t.Fatalf("insert valid expression fixture: %v", err)
	}

	assertCheckViolation(t, `
		INSERT INTO expression_tags (expression_id, tag)
		VALUES ($1, 'Invalid Tag')
	`, expressionID)
	assertCheckViolation(t, `
		INSERT INTO expression_review_state (expression_id, interval_days, due_at, updated_at)
		VALUES ($1, 2, $2, $2)
	`, expressionID, now)
	assertCheckViolation(t, `
		INSERT INTO review_events (
			expression_id, rating, reviewed_at,
			previous_interval_days, next_interval_days, created_at
		)
		VALUES ($1, 'easy', $2, 1, 3, $2)
	`, expressionID, now)
}

func assertCheckViolation(t *testing.T, statement string, arguments ...any) {
	t.Helper()
	_, err := integrationPool.Exec(t.Context(), statement, arguments...)
	if err == nil {
		t.Fatal("database write succeeded, want check-constraint rejection")
	}
	var postgresError *pgconn.PgError
	if !errors.As(err, &postgresError) {
		t.Fatalf("database write error = %T %v, want PostgreSQL error", err, err)
	}
	if postgresError.Code != "23514" {
		t.Fatalf("database write SQLSTATE = %s, want 23514 (check_violation): %v", postgresError.Code, postgresError)
	}
}
