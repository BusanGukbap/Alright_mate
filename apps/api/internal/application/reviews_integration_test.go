package application

import (
	"context"
	"errors"
	"reflect"
	"sort"
	"testing"
	"time"

	"alrightmate/api/internal/review"
	"github.com/google/uuid"
)

func TestDueReviewsUseClockOrderingLimitAndPublishedVisibility(t *testing.T) {
	pool := requireIntegration(t)
	now := time.Date(2026, time.August, 8, 12, 0, 0, 0, time.UTC)
	clock := &fixedClock{}
	service := NewService(pool, clock)

	clock.Set(now.Add(-48 * time.Hour))
	earliest := createPublishedExpression(t, service, CreateExpressionInput{Expression: "Earliest due", MeaningKo: "가장 먼저", Tags: []string{"due"}})
	archived := createPublishedExpression(t, service, CreateExpressionInput{Expression: "Archived due", MeaningKo: "보관됨"})
	clock.Set(now.Add(-24 * time.Hour))
	firstTie := createPublishedExpression(t, service, CreateExpressionInput{Expression: "Same due one", MeaningKo: "동일 시각 하나"})
	secondTie := createPublishedExpression(t, service, CreateExpressionInput{Expression: "Same due two", MeaningKo: "동일 시각 둘"})
	clock.Set(now)
	createPublishedExpression(t, service, CreateExpressionInput{Expression: "Not due", MeaningKo: "아직 아님"})
	if _, err := service.CreateExpression(t.Context(), CreateExpressionInput{Expression: "Draft excluded", MeaningKo: "초안 제외"}); err != nil {
		t.Fatalf("create draft fixture: %v", err)
	}
	archivedStatus := StatusArchived
	if _, err := service.UpdateExpression(t.Context(), archived.ID, UpdateExpressionInput{Status: &archivedStatus}); err != nil {
		t.Fatalf("archive due fixture: %v", err)
	}

	due, err := service.ListDueReviews(t.Context(), 2)
	if err != nil {
		t.Fatalf("list limited due reviews: %v", err)
	}
	if len(due) != 2 || due[0].Expression.ID != earliest.ID {
		t.Fatalf("limited due reviews = %+v, want earliest first and two rows", due)
	}
	tiedIDs := []string{firstTie.ID.String(), secondTie.ID.String()}
	sort.Strings(tiedIDs)
	if due[1].Expression.ID.String() != tiedIDs[0] {
		t.Fatalf("tie ordering second id = %s, want expression_id ASC %s", due[1].Expression.ID, tiedIDs[0])
	}
	if due[0].ReviewState.IntervalDays != 1 || !due[0].ReviewState.DueAt.Equal(now.Add(-24*time.Hour)) {
		t.Fatalf("earliest due review state = %+v", due[0].ReviewState)
	}

	allDue, err := service.ListDueReviews(t.Context(), 100)
	if err != nil {
		t.Fatalf("list all due reviews: %v", err)
	}
	if len(allDue) != 3 {
		t.Fatalf("all due review count = %d, want 3", len(allDue))
	}
	for _, invalid := range []int32{0, 101} {
		if _, err := service.ListDueReviews(t.Context(), invalid); !errors.Is(err, ErrValidation) {
			t.Fatalf("ListDueReviews(%d) error = %v, want ErrValidation", invalid, err)
		}
	}
}

func TestRecordReviewAdvancesEveryIntervalPathAndUsesDatabaseSeoulDate(t *testing.T) {
	pool := requireIntegration(t)
	reviewedAt := time.Date(2026, time.August, 8, 15, 30, 0, 0, time.UTC)
	clock := &fixedClock{now: reviewedAt.Add(-48 * time.Hour)}
	service := NewService(pool, clock)

	tests := []struct {
		name    string
		current int16
		rating  review.Rating
		want    int16
	}{
		{name: "again 1 to 1", current: 1, rating: review.RatingAgain, want: 1},
		{name: "again 3 to 1", current: 3, rating: review.RatingAgain, want: 1},
		{name: "again 7 to 1", current: 7, rating: review.RatingAgain, want: 1},
		{name: "again 14 to 1", current: 14, rating: review.RatingAgain, want: 1},
		{name: "again 30 to 1", current: 30, rating: review.RatingAgain, want: 1},
		{name: "good 1 to 3", current: 1, rating: review.RatingGood, want: 3},
		{name: "good 3 to 7", current: 3, rating: review.RatingGood, want: 7},
		{name: "good 7 to 14", current: 7, rating: review.RatingGood, want: 14},
		{name: "good 14 to 30", current: 14, rating: review.RatingGood, want: 30},
		{name: "good 30 stays 30", current: 30, rating: review.RatingGood, want: 30},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			clock.Set(reviewedAt.Add(-48 * time.Hour))
			expression := createPublishedExpression(t, service, CreateExpressionInput{
				Expression: "Interval path " + test.name,
				MeaningKo:  "간격 전환",
			})
			if _, err := pool.Exec(t.Context(), `
				UPDATE expression_review_state
				SET interval_days = $2
				WHERE expression_id = $1
			`, expression.ID, test.current); err != nil {
				t.Fatalf("set current interval: %v", err)
			}
			clock.Set(reviewedAt)

			result, err := service.RecordReview(t.Context(), expression.ID, test.rating)
			if err != nil {
				t.Fatalf("RecordReview(): %v", err)
			}
			if result.ExpressionID != expression.ID || result.Rating != test.rating || result.PreviousIntervalDays != test.current || result.NextIntervalDays != test.want {
				t.Fatalf("review transition result = %+v", result)
			}
			if !result.ReviewedAt.Equal(reviewedAt) || result.ReviewedAt.Location() != time.UTC {
				t.Fatalf("reviewedAt = %s (%s), want UTC %s", result.ReviewedAt, result.ReviewedAt.Location(), reviewedAt)
			}
			if result.ReviewDateSeoul != "2026-08-09" {
				t.Fatalf("database Seoul date = %q, want 2026-08-09", result.ReviewDateSeoul)
			}
			wantDue := reviewedAt.Add(time.Duration(test.want) * 24 * time.Hour)
			if result.ReviewState.IntervalDays != test.want || !result.ReviewState.DueAt.Equal(wantDue) || result.ReviewState.LastReviewedAt == nil || !result.ReviewState.LastReviewedAt.Equal(reviewedAt) {
				t.Fatalf("updated review state = %+v, want interval %d due %s", result.ReviewState, test.want, wantDue)
			}
			if result.EventID == uuid.Nil {
				t.Fatal("review event id is nil UUID")
			}
		})
	}

	clock.Set(time.Date(2026, time.August, 7, 0, 0, 0, 0, time.UTC))
	beforeBoundary := createPublishedExpression(t, service, CreateExpressionInput{Expression: "Before Seoul boundary", MeaningKo: "서울 날짜 경계 전"})
	clock.Set(time.Date(2026, time.August, 8, 14, 30, 0, 0, time.UTC))
	result, err := service.RecordReview(t.Context(), beforeBoundary.ID, review.RatingAgain)
	if err != nil {
		t.Fatalf("record pre-boundary review: %v", err)
	}
	if result.ReviewDateSeoul != "2026-08-08" {
		t.Fatalf("pre-boundary Seoul date = %q, want 2026-08-08", result.ReviewDateSeoul)
	}
}

func TestConcurrentGoodReviewsOnDistinctSeoulDatesAdvanceSequentially(t *testing.T) {
	pool := requireIntegration(t)
	creator := NewService(pool, &fixedClock{now: time.Date(2026, time.August, 6, 0, 0, 0, 0, time.UTC)})
	expression := createPublishedExpression(t, creator, CreateExpressionInput{
		Expression: "Concurrent review locking",
		MeaningKo:  "sequential interval proof",
	})
	installTemporaryDatabaseObjects(t, `
		CREATE FUNCTION sleep_before_concurrent_review_insert() RETURNS trigger LANGUAGE plpgsql AS $$
		BEGIN
			PERFORM pg_sleep(0.75);
			RETURN NEW;
		END
		$$;
		CREATE TRIGGER sleep_before_concurrent_review_insert_trigger
		BEFORE INSERT ON review_events
		FOR EACH ROW EXECUTE FUNCTION sleep_before_concurrent_review_insert();
	`, `
		DROP TRIGGER IF EXISTS sleep_before_concurrent_review_insert_trigger ON review_events;
		DROP FUNCTION IF EXISTS sleep_before_concurrent_review_insert();
	`)

	services := []*Service{
		NewService(pool, &fixedClock{now: time.Date(2026, time.August, 8, 1, 0, 0, 0, time.UTC)}),
		NewService(pool, &fixedClock{now: time.Date(2026, time.August, 9, 1, 0, 0, 0, time.UTC)}),
	}
	type outcome struct {
		result ReviewEventResult
		err    error
	}
	ctx, cancel := context.WithTimeout(t.Context(), 10*time.Second)
	defer cancel()
	start := make(chan struct{})
	outcomes := make(chan outcome, len(services))
	for _, service := range services {
		service := service
		go func() {
			<-start
			result, err := service.RecordReview(ctx, expression.ID, review.RatingGood)
			outcomes <- outcome{result: result, err: err}
		}()
	}
	close(start)

	results := make([]ReviewEventResult, 0, len(services))
	for range services {
		select {
		case completed := <-outcomes:
			if completed.err != nil {
				t.Fatalf("concurrent RecordReview() error: %v", completed.err)
			}
			results = append(results, completed.result)
		case <-ctx.Done():
			t.Fatalf("concurrent reviews did not complete: %v", ctx.Err())
		}
	}
	sort.Slice(results, func(left, right int) bool {
		return results[left].PreviousIntervalDays < results[right].PreviousIntervalDays
	})
	if results[0].PreviousIntervalDays != 1 || results[0].NextIntervalDays != 3 || results[1].PreviousIntervalDays != 3 || results[1].NextIntervalDays != 7 {
		t.Fatalf("concurrent transitions = %d->%d and %d->%d, want 1->3 then 3->7", results[0].PreviousIntervalDays, results[0].NextIntervalDays, results[1].PreviousIntervalDays, results[1].NextIntervalDays)
	}
	dates := []string{results[0].ReviewDateSeoul, results[1].ReviewDateSeoul}
	sort.Strings(dates)
	if !reflect.DeepEqual(dates, []string{"2026-08-08", "2026-08-09"}) {
		t.Fatalf("concurrent review Seoul dates = %v", dates)
	}
	var finalInterval int16
	if err := pool.QueryRow(t.Context(), `
		SELECT interval_days
		FROM expression_review_state
		WHERE expression_id = $1
	`, expression.ID).Scan(&finalInterval); err != nil {
		t.Fatalf("read final concurrent review state: %v", err)
	}
	if finalInterval != 7 {
		t.Fatalf("final concurrent interval = %d, want 7", finalInterval)
	}
}

func TestDuplicateReviewReturnsStableConflictAndRollsBackStateAndEvent(t *testing.T) {
	pool := requireIntegration(t)
	clock := &fixedClock{now: time.Date(2026, time.August, 7, 0, 0, 0, 0, time.UTC)}
	service := NewService(pool, clock)
	expression := createPublishedExpression(t, service, CreateExpressionInput{Expression: "Duplicate review", MeaningKo: "중복 복습"})

	clock.Set(time.Date(2026, time.August, 8, 14, 0, 0, 0, time.UTC))
	first, err := service.RecordReview(t.Context(), expression.ID, review.RatingGood)
	if err != nil {
		t.Fatalf("record first review: %v", err)
	}
	clock.Set(time.Date(2026, time.August, 8, 14, 30, 0, 0, time.UTC))
	if _, err := service.RecordReview(t.Context(), expression.ID, review.RatingAgain); !errors.Is(err, ErrReviewAlreadyRecorded) {
		t.Fatalf("duplicate review error = %v, want ErrReviewAlreadyRecorded", err)
	}

	var (
		interval int16
		dueAt    time.Time
		lastAt   time.Time
		updated  time.Time
		events   int
	)
	if err := pool.QueryRow(t.Context(), `
		SELECT interval_days, due_at, last_reviewed_at, updated_at
		FROM expression_review_state
		WHERE expression_id = $1
	`, expression.ID).Scan(&interval, &dueAt, &lastAt, &updated); err != nil {
		t.Fatalf("read state after duplicate: %v", err)
	}
	if err := pool.QueryRow(t.Context(), `SELECT count(*) FROM review_events WHERE expression_id = $1`, expression.ID).Scan(&events); err != nil {
		t.Fatalf("count events after duplicate: %v", err)
	}
	if interval != first.ReviewState.IntervalDays || !dueAt.Equal(first.ReviewState.DueAt) || !lastAt.Equal(*first.ReviewState.LastReviewedAt) || !updated.Equal(first.ReviewState.UpdatedAt) || events != 1 {
		t.Fatalf("duplicate mutated state/event: interval=%d due=%s last=%s updated=%s events=%d first=%+v", interval, dueAt, lastAt, updated, events, first)
	}

	clock.Set(time.Date(2026, time.August, 8, 15, 30, 0, 0, time.UTC))
	if _, err := service.RecordReview(t.Context(), expression.ID, review.RatingAgain); err != nil {
		t.Fatalf("record next-Seoul-date review: %v", err)
	}
}

func TestUnknownDraftAndArchivedExpressionsAreNotReviewable(t *testing.T) {
	pool := requireIntegration(t)
	clock := &fixedClock{now: time.Date(2026, time.August, 8, 0, 0, 0, 0, time.UTC)}
	service := NewService(pool, clock)
	draft, err := service.CreateExpression(t.Context(), CreateExpressionInput{Expression: "Draft review", MeaningKo: "초안"})
	if err != nil {
		t.Fatalf("create draft: %v", err)
	}
	archived := createPublishedExpression(t, service, CreateExpressionInput{Expression: "Archived review", MeaningKo: "보관"})
	archivedStatus := StatusArchived
	if _, err := service.UpdateExpression(t.Context(), archived.ID, UpdateExpressionInput{Status: &archivedStatus}); err != nil {
		t.Fatalf("archive review fixture: %v", err)
	}

	for name, id := range map[string]uuid.UUID{"unknown": uuid.New(), "draft": draft.ID, "archived": archived.ID} {
		t.Run(name, func(t *testing.T) {
			if _, err := service.RecordReview(t.Context(), id, review.RatingGood); !errors.Is(err, ErrNotFound) {
				t.Fatalf("RecordReview() error = %v, want ErrNotFound", err)
			}
		})
	}
}

func TestProgressUsesConsecutiveSeoulDatesZeroFillAndBalancedRatings(t *testing.T) {
	pool := requireIntegration(t)
	clock := &fixedClock{}
	service := NewService(pool, clock)

	recordProgressFixture(t, service, clock, "Progress again one", time.Date(2026, time.August, 5, 1, 0, 0, 0, time.UTC), review.RatingAgain)
	recordProgressFixture(t, service, clock, "Progress good", time.Date(2026, time.August, 7, 1, 0, 0, 0, time.UTC), review.RatingGood)
	recordProgressFixture(t, service, clock, "Progress again two", time.Date(2026, time.August, 7, 2, 0, 0, 0, time.UTC), review.RatingAgain)

	clock.Set(time.Date(2026, time.August, 9, 3, 0, 0, 0, time.UTC))
	progress, err := service.GetReviewProgress(t.Context(), 5)
	if err != nil {
		t.Fatalf("get five-day progress: %v", err)
	}
	want := ReviewProgress{
		Days:      5,
		StartDate: "2026-08-05",
		EndDate:   "2026-08-09",
		Total:     3,
		Entries: []ProgressDay{
			{Date: "2026-08-05", Total: 1, Again: 1, Good: 0},
			{Date: "2026-08-06", Total: 0, Again: 0, Good: 0},
			{Date: "2026-08-07", Total: 2, Again: 1, Good: 1},
			{Date: "2026-08-08", Total: 0, Again: 0, Good: 0},
			{Date: "2026-08-09", Total: 0, Again: 0, Good: 0},
		},
	}
	if !reflect.DeepEqual(progress, want) {
		t.Fatalf("five-day progress = %+v, want %+v", progress, want)
	}

	oneDay, err := service.GetReviewProgress(t.Context(), 1)
	if err != nil {
		t.Fatalf("get one-day progress: %v", err)
	}
	if len(oneDay.Entries) != 1 || oneDay.StartDate != "2026-08-09" || oneDay.EndDate != "2026-08-09" {
		t.Fatalf("one-day progress = %+v", oneDay)
	}
	ninetyDays, err := service.GetReviewProgress(t.Context(), 90)
	if err != nil {
		t.Fatalf("get ninety-day progress: %v", err)
	}
	if len(ninetyDays.Entries) != 90 || ninetyDays.EndDate != "2026-08-09" || ninetyDays.StartDate != "2026-05-12" {
		t.Fatalf("ninety-day range = %s..%s (%d entries)", ninetyDays.StartDate, ninetyDays.EndDate, len(ninetyDays.Entries))
	}
	for _, invalid := range []int32{0, 91} {
		if _, err := service.GetReviewProgress(t.Context(), invalid); !errors.Is(err, ErrValidation) {
			t.Fatalf("GetReviewProgress(%d) error = %v, want ErrValidation", invalid, err)
		}
	}
}

func recordProgressFixture(t *testing.T, service *Service, clock *fixedClock, expressionText string, reviewedAt time.Time, rating review.Rating) {
	t.Helper()
	clock.Set(reviewedAt.Add(-48 * time.Hour))
	expression := createPublishedExpression(t, service, CreateExpressionInput{Expression: expressionText, MeaningKo: "진행 데이터"})
	clock.Set(reviewedAt)
	if _, err := service.RecordReview(t.Context(), expression.ID, rating); err != nil {
		t.Fatalf("record progress fixture %q: %v", expressionText, err)
	}
}
