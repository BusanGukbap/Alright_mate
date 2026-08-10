package application

import (
	"context"
	"errors"
	"fmt"
	"time"

	storage "alrightmate/api/internal/storage/sqlc"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgtype"
)

func (service *Service) withTransaction(ctx context.Context, options pgx.TxOptions, operation func(*storage.Queries) error) error {
	transaction, err := service.pool.BeginTx(ctx, options)
	if err != nil {
		return fmt.Errorf("begin PostgreSQL transaction: %w", err)
	}
	defer func() {
		_ = transaction.Rollback(ctx)
	}()

	if err := operation(storage.New(transaction)); err != nil {
		return err
	}
	if err := transaction.Commit(ctx); err != nil {
		return fmt.Errorf("commit PostgreSQL transaction: %w", err)
	}
	return nil
}

func mapStorageError(err error) error {
	if err == nil || errors.Is(err, ErrValidation) || errors.Is(err, ErrNotFound) || errors.Is(err, ErrInvalidTransition) || errors.Is(err, ErrExpressionConflict) || errors.Is(err, ErrReviewAlreadyRecorded) {
		return err
	}
	if errors.Is(err, pgx.ErrNoRows) {
		return ErrNotFound
	}
	var postgresError *pgconn.PgError
	if errors.As(err, &postgresError) && postgresError.Code == "23505" {
		switch postgresError.ConstraintName {
		case "expressions_lower_expression_key":
			return fmt.Errorf("%w: %v", ErrExpressionConflict, err)
		case "review_events_expression_review_date_seoul_key":
			return fmt.Errorf("%w: %v", ErrReviewAlreadyRecorded, err)
		}
	}
	return err
}

func databaseTimestamp(value time.Time) pgtype.Timestamptz {
	return pgtype.Timestamptz{Time: value.UTC(), Valid: true}
}

func nullableDatabaseTimestamp(value *time.Time) pgtype.Timestamptz {
	if value == nil {
		return pgtype.Timestamptz{}
	}
	return databaseTimestamp(*value)
}

func timestampValue(value pgtype.Timestamptz) time.Time {
	return value.Time.UTC()
}

func nullableTimestampValue(value pgtype.Timestamptz) *time.Time {
	if !value.Valid {
		return nil
	}
	instant := value.Time.UTC()
	return &instant
}

func nonNilTags(tags []string) []string {
	if tags == nil {
		return []string{}
	}
	return tags
}
