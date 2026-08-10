package application

import (
	"context"
	"fmt"
	"time"
	_ "time/tzdata"

	"alrightmate/api/internal/review"
	storage "alrightmate/api/internal/storage/sqlc"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

var seoulLocation = mustLoadSeoulLocation()

func (service *Service) ListDueReviews(ctx context.Context, limit int32) ([]DueReview, error) {
	if limit < 1 || limit > 100 {
		return nil, fmt.Errorf("%w: due review limit must be 1 through 100", ErrValidation)
	}
	rows, err := storage.New(service.pool).ListDueReviews(ctx, storage.ListDueReviewsParams{
		Now:      databaseTimestamp(service.clock.Now()),
		RowLimit: limit,
	})
	if err != nil {
		return nil, mapStorageError(err)
	}
	result := make([]DueReview, len(rows))
	for index, row := range rows {
		result[index] = DueReview{
			Expression: DueReviewExpression{
				ID:          row.ID,
				Expression:  row.Expression,
				MeaningKo:   row.MeaningKo,
				Note:        row.Note,
				Tags:        nonNilTags(row.Tags),
				SourceURL:   row.SourceUrl,
				Status:      StatusPublished,
				PublishedAt: timestampValue(row.PublishedAt),
			},
			ReviewState: ReviewState{
				IntervalDays:   row.IntervalDays,
				DueAt:          timestampValue(row.DueAt),
				LastReviewedAt: nullableTimestampValue(row.LastReviewedAt),
				UpdatedAt:      timestampValue(row.ReviewUpdatedAt),
			},
		}
	}
	return result, nil
}

func (service *Service) RecordReview(ctx context.Context, expressionID uuid.UUID, rating review.Rating) (ReviewEventResult, error) {
	reviewedAt := service.clock.Now().UTC()
	var result ReviewEventResult
	err := service.withTransaction(ctx, pgx.TxOptions{}, func(queries *storage.Queries) error {
		current, err := queries.GetPublishedReviewStateForUpdate(ctx, expressionID)
		if err != nil {
			return err
		}
		nextInterval, err := review.NextInterval(current.IntervalDays, rating)
		if err != nil {
			return fmt.Errorf("%w: %v", ErrValidation, err)
		}
		dueAt, err := review.NextDueAt(reviewedAt, nextInterval)
		if err != nil {
			return err
		}

		event, err := queries.InsertReviewEvent(ctx, storage.InsertReviewEventParams{
			ExpressionID:         expressionID,
			Rating:               string(rating),
			ReviewedAt:           databaseTimestamp(reviewedAt),
			PreviousIntervalDays: current.IntervalDays,
			NextIntervalDays:     nextInterval,
			CreatedAt:            databaseTimestamp(reviewedAt),
		})
		if err != nil {
			return err
		}
		state, err := queries.UpdateReviewState(ctx, storage.UpdateReviewStateParams{
			IntervalDays:   nextInterval,
			DueAt:          databaseTimestamp(dueAt),
			LastReviewedAt: databaseTimestamp(reviewedAt),
			UpdatedAt:      databaseTimestamp(reviewedAt),
			ExpressionID:   expressionID,
		})
		if err != nil {
			return err
		}
		result = ReviewEventResult{
			EventID:              event.ID,
			ExpressionID:         event.ExpressionID,
			Rating:               review.Rating(event.Rating),
			ReviewedAt:           timestampValue(event.ReviewedAt),
			ReviewDateSeoul:      event.ReviewDateSeoul.Format(time.DateOnly),
			PreviousIntervalDays: event.PreviousIntervalDays,
			NextIntervalDays:     event.NextIntervalDays,
			ReviewState:          reviewStateFromStorage(state),
		}
		return nil
	})
	return result, mapStorageError(err)
}

func (service *Service) GetReviewProgress(ctx context.Context, days int32) (ReviewProgress, error) {
	if days < 1 || days > 90 {
		return ReviewProgress{}, fmt.Errorf("%w: progress days must be 1 through 90", ErrValidation)
	}
	nowSeoul := service.clock.Now().In(seoulLocation)
	endDate := time.Date(nowSeoul.Year(), nowSeoul.Month(), nowSeoul.Day(), 0, 0, 0, 0, time.UTC)
	startDate := endDate.AddDate(0, 0, -int(days)+1)

	rows, err := storage.New(service.pool).ListProgressCounts(ctx, storage.ListProgressCountsParams{
		StartDate: startDate,
		EndDate:   endDate,
	})
	if err != nil {
		return ReviewProgress{}, mapStorageError(err)
	}
	counts := make(map[string]ProgressDay, len(rows))
	for _, row := range rows {
		date := row.ReviewDateSeoul.Format(time.DateOnly)
		counts[date] = ProgressDay{Date: date, Total: row.Total, Again: row.Again, Good: row.Good}
	}

	result := ReviewProgress{
		Days:      days,
		StartDate: startDate.Format(time.DateOnly),
		EndDate:   endDate.Format(time.DateOnly),
		Entries:   make([]ProgressDay, 0, days),
	}
	for offset := 0; offset < int(days); offset++ {
		date := startDate.AddDate(0, 0, offset).Format(time.DateOnly)
		entry, exists := counts[date]
		if !exists {
			entry = ProgressDay{Date: date}
		}
		result.Total += entry.Total
		result.Entries = append(result.Entries, entry)
	}
	return result, nil
}

func mustLoadSeoulLocation() *time.Location {
	location, err := time.LoadLocation("Asia/Seoul")
	if err != nil {
		panic(fmt.Sprintf("load Asia/Seoul timezone: %v", err))
	}
	return location
}
