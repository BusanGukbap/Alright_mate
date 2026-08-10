package application

import (
	"context"
	"errors"
	"fmt"
	"time"

	"alrightmate/api/internal/review"
	storage "alrightmate/api/internal/storage/sqlc"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

func (service *Service) CreateExpression(ctx context.Context, input CreateExpressionInput) (Expression, error) {
	normalised, err := normaliseCreateInput(input)
	if err != nil {
		return Expression{}, err
	}
	now := service.clock.Now().UTC()
	var publishedAt *time.Time
	if normalised.status == StatusPublished {
		publishedAt = &now
	}

	var result Expression
	err = service.withTransaction(ctx, pgx.TxOptions{}, func(queries *storage.Queries) error {
		row, err := queries.InsertExpression(ctx, storage.InsertExpressionParams{
			Expression:  normalised.expression,
			MeaningKo:   normalised.meaningKo,
			Note:        normalised.note,
			SourceUrl:   normalised.sourceURL,
			Status:      string(normalised.status),
			CreatedAt:   databaseTimestamp(now),
			UpdatedAt:   databaseTimestamp(now),
			PublishedAt: nullableDatabaseTimestamp(publishedAt),
		})
		if err != nil {
			return err
		}
		if err := replaceExpressionTags(ctx, queries, row.ID, normalised.tags); err != nil {
			return err
		}
		if normalised.status == StatusPublished {
			dueAt, err := review.NextDueAt(now, 1)
			if err != nil {
				return err
			}
			if _, err := queries.InsertInitialReviewState(ctx, storage.InsertInitialReviewStateParams{
				ExpressionID: row.ID,
				DueAt:        databaseTimestamp(dueAt),
				UpdatedAt:    databaseTimestamp(now),
			}); err != nil {
				return err
			}
		}
		result, err = loadExpression(ctx, queries, row.ID)
		return err
	})
	return result, mapStorageError(err)
}

func (service *Service) UpdateExpression(ctx context.Context, id uuid.UUID, input UpdateExpressionInput) (Expression, error) {
	if input.Expression == nil && input.MeaningKo == nil && !input.Note.Set && !input.SourceURL.Set && input.Tags == nil && input.Status == nil {
		return Expression{}, fmt.Errorf("%w: empty expression patch", ErrValidation)
	}

	now := service.clock.Now().UTC()
	var result Expression
	err := service.withTransaction(ctx, pgx.TxOptions{}, func(queries *storage.Queries) error {
		current, err := queries.GetExpressionForUpdate(ctx, id)
		if err != nil {
			return err
		}
		currentStatus := Status(current.Status)
		if currentStatus == StatusArchived {
			return fmt.Errorf("%w: archived expressions cannot be amended", ErrInvalidTransition)
		}

		expression := current.Expression
		if input.Expression != nil {
			expression, err = requiredText("expression", *input.Expression, 300)
			if err != nil {
				return err
			}
		}
		meaningKo := current.MeaningKo
		if input.MeaningKo != nil {
			meaningKo, err = requiredText("meaningKo", *input.MeaningKo, 1000)
			if err != nil {
				return err
			}
		}
		note := current.Note
		if input.Note.Set {
			note = input.Note.Value
			if err := validateNote(note); err != nil {
				return err
			}
		}
		sourceURL := current.SourceUrl
		if input.SourceURL.Set {
			sourceURL = input.SourceURL.Value
			if err := validateSourceURL(sourceURL); err != nil {
				return err
			}
		}
		tags := current.Tags
		if input.Tags != nil {
			tags, err = normaliseTags(*input.Tags)
			if err != nil {
				return err
			}
		}

		targetStatus := currentStatus
		publishing := false
		archiving := false
		if input.Status != nil {
			targetStatus = *input.Status
			switch {
			case currentStatus == StatusDraft && targetStatus == StatusPublished:
				publishing = true
			case currentStatus == StatusPublished && targetStatus == StatusArchived:
				archiving = true
			default:
				return fmt.Errorf("%w: %s to %s", ErrInvalidTransition, currentStatus, targetStatus)
			}
		}
		if (targetStatus == StatusPublished || targetStatus == StatusArchived) && sourceURL == nil {
			return fmt.Errorf("%w: published expression requires sourceUrl", ErrValidation)
		}
		if err := validateSourceURL(sourceURL); err != nil {
			return err
		}

		publishedAt := nullableTimestampValue(current.PublishedAt)
		archivedAt := nullableTimestampValue(current.ArchivedAt)
		if publishing {
			publishedAt = &now
		}
		if archiving {
			archivedAt = &now
		}
		if _, err := queries.UpdateExpression(ctx, storage.UpdateExpressionParams{
			Expression:  expression,
			MeaningKo:   meaningKo,
			Note:        note,
			SourceUrl:   sourceURL,
			Status:      string(targetStatus),
			UpdatedAt:   databaseTimestamp(now),
			PublishedAt: nullableDatabaseTimestamp(publishedAt),
			ArchivedAt:  nullableDatabaseTimestamp(archivedAt),
			ID:          id,
		}); err != nil {
			return err
		}
		if input.Tags != nil {
			if err := replaceExpressionTags(ctx, queries, id, tags); err != nil {
				return err
			}
		}
		if publishing {
			dueAt, err := review.NextDueAt(now, 1)
			if err != nil {
				return err
			}
			if _, err := queries.InsertInitialReviewState(ctx, storage.InsertInitialReviewStateParams{
				ExpressionID: id,
				DueAt:        databaseTimestamp(dueAt),
				UpdatedAt:    databaseTimestamp(now),
			}); err != nil {
				return err
			}
		}
		result, err = loadExpression(ctx, queries, id)
		return err
	})
	return result, mapStorageError(err)
}

func (service *Service) ListPublicExpressions(ctx context.Context, input ListPublicExpressionsInput) (PublicExpressionPage, error) {
	if input.Page < 1 || input.PageSize < 1 || input.PageSize > 100 {
		return PublicExpressionPage{}, fmt.Errorf("%w: invalid pagination", ErrValidation)
	}
	query, err := normaliseOptionalQuery(input.Query)
	if err != nil {
		return PublicExpressionPage{}, err
	}
	tag, err := normaliseOptionalTag(input.Tag)
	if err != nil {
		return PublicExpressionPage{}, err
	}

	page := PublicExpressionPage{
		Items:    []PublicExpressionListItem{},
		Page:     input.Page,
		PageSize: input.PageSize,
	}
	err = service.withTransaction(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly}, func(queries *storage.Queries) error {
		total, err := queries.CountPublicExpressions(ctx, storage.CountPublicExpressionsParams{Query: query, Tag: tag})
		if err != nil {
			return err
		}
		rows, err := queries.ListPublicExpressions(ctx, storage.ListPublicExpressionsParams{
			Query:     query,
			Tag:       tag,
			RowOffset: int64(input.Page-1) * int64(input.PageSize),
			RowLimit:  input.PageSize,
		})
		if err != nil {
			return err
		}
		page.Total = total
		page.Items = make([]PublicExpressionListItem, len(rows))
		for index, row := range rows {
			page.Items[index] = PublicExpressionListItem{
				ID:          row.ID,
				Expression:  row.Expression,
				MeaningKo:   row.MeaningKo,
				Tags:        nonNilTags(row.Tags),
				SourceURL:   row.SourceUrl,
				PublishedAt: timestampValue(row.PublishedAt),
			}
		}
		return nil
	})
	return page, mapStorageError(err)
}

func (service *Service) GetPublicExpression(ctx context.Context, id uuid.UUID) (PublicExpressionDetail, error) {
	row, err := storage.New(service.pool).GetPublicExpression(ctx, id)
	if err != nil {
		return PublicExpressionDetail{}, mapStorageError(err)
	}
	return PublicExpressionDetail{
		ID:          row.ID,
		Expression:  row.Expression,
		MeaningKo:   row.MeaningKo,
		Note:        row.Note,
		Tags:        nonNilTags(row.Tags),
		SourceURL:   row.SourceUrl,
		PublishedAt: timestampValue(row.PublishedAt),
	}, nil
}

func replaceExpressionTags(ctx context.Context, queries *storage.Queries, expressionID uuid.UUID, tags []string) error {
	if err := queries.DeleteExpressionTags(ctx, expressionID); err != nil {
		return err
	}
	for _, tag := range tags {
		if err := queries.InsertExpressionTag(ctx, storage.InsertExpressionTagParams{ExpressionID: expressionID, Tag: tag}); err != nil {
			return err
		}
	}
	return nil
}

func loadExpression(ctx context.Context, queries *storage.Queries, id uuid.UUID) (Expression, error) {
	row, err := queries.GetExpression(ctx, id)
	if err != nil {
		return Expression{}, err
	}
	result := Expression{
		ID:          row.ID,
		Expression:  row.Expression,
		MeaningKo:   row.MeaningKo,
		Note:        row.Note,
		SourceURL:   row.SourceUrl,
		Tags:        nonNilTags(row.Tags),
		Status:      Status(row.Status),
		CreatedAt:   timestampValue(row.CreatedAt),
		UpdatedAt:   timestampValue(row.UpdatedAt),
		PublishedAt: nullableTimestampValue(row.PublishedAt),
		ArchivedAt:  nullableTimestampValue(row.ArchivedAt),
	}
	if result.Status == StatusDraft {
		return result, nil
	}
	state, err := queries.GetReviewState(ctx, id)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return Expression{}, fmt.Errorf("published expression %s has no review state", id)
		}
		return Expression{}, err
	}
	converted := reviewStateFromStorage(state)
	result.ReviewState = &converted
	return result, nil
}

func reviewStateFromStorage(state storage.ExpressionReviewState) ReviewState {
	return ReviewState{
		IntervalDays:   state.IntervalDays,
		DueAt:          timestampValue(state.DueAt),
		LastReviewedAt: nullableTimestampValue(state.LastReviewedAt),
		UpdatedAt:      timestampValue(state.UpdatedAt),
	}
}
