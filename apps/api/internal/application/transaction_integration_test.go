package application

import (
	"reflect"
	"testing"
	"time"

	"alrightmate/api/internal/review"
	"github.com/jackc/pgx/v5/pgxpool"
)

func TestCreateExpressionRollsBackInsertWhenTagInsertFails(t *testing.T) {
	pool := requireIntegration(t)
	service := NewService(pool, &fixedClock{now: time.Date(2026, time.August, 8, 0, 0, 0, 0, time.UTC)})
	installTemporaryDatabaseObjects(t, `
		CREATE FUNCTION fail_selected_tag_insert() RETURNS trigger LANGUAGE plpgsql AS $$
		BEGIN
			IF NEW.tag = 'reject-tag' THEN
				RAISE EXCEPTION 'injected tag insert failure';
			END IF;
			RETURN NEW;
		END
		$$;
		CREATE TRIGGER fail_selected_tag_insert_trigger
		BEFORE INSERT ON expression_tags
		FOR EACH ROW EXECUTE FUNCTION fail_selected_tag_insert();
	`, `
		DROP TRIGGER IF EXISTS fail_selected_tag_insert_trigger ON expression_tags;
		DROP FUNCTION IF EXISTS fail_selected_tag_insert();
	`)

	_, err := service.CreateExpression(t.Context(), CreateExpressionInput{
		Expression: "Create tag rollback",
		MeaningKo:  "transaction rollback",
		Tags:       []string{"stable", "reject-tag"},
	})
	if err == nil {
		t.Fatal("CreateExpression() error = nil, want injected tag failure")
	}
	if got := countDatabaseRows(t, pool, `SELECT count(*) FROM expressions WHERE expression = 'Create tag rollback'`); got != 0 {
		t.Fatalf("expression rows after tag failure = %d, want 0", got)
	}
	if got := countDatabaseRows(t, pool, `SELECT count(*) FROM expression_tags`); got != 0 {
		t.Fatalf("tag rows after tag failure = %d, want 0", got)
	}
}

func TestDirectPublishRollsBackExpressionAndTagsWhenInitialStateInsertFails(t *testing.T) {
	pool := requireIntegration(t)
	service := NewService(pool, &fixedClock{now: time.Date(2026, time.August, 8, 1, 0, 0, 0, time.UTC)})
	installTemporaryDatabaseObjects(t, `
		CREATE FUNCTION fail_initial_state_insert() RETURNS trigger LANGUAGE plpgsql AS $$
		BEGIN
			RAISE EXCEPTION 'injected initial state failure';
		END
		$$;
		CREATE TRIGGER fail_initial_state_insert_trigger
		BEFORE INSERT ON expression_review_state
		FOR EACH ROW EXECUTE FUNCTION fail_initial_state_insert();
	`, `
		DROP TRIGGER IF EXISTS fail_initial_state_insert_trigger ON expression_review_state;
		DROP FUNCTION IF EXISTS fail_initial_state_insert();
	`)

	_, err := service.CreateExpression(t.Context(), CreateExpressionInput{
		Expression: "Direct publish rollback",
		MeaningKo:  "transaction rollback",
		SourceURL:  stringPointer("https://example.com/direct-publish"),
		Tags:       []string{"published", "rollback"},
		Status:     StatusPublished,
	})
	if err == nil {
		t.Fatal("CreateExpression() error = nil, want injected initial-state failure")
	}
	for table, query := range map[string]string{
		"expression":   `SELECT count(*) FROM expressions WHERE expression = 'Direct publish rollback'`,
		"tags":         `SELECT count(*) FROM expression_tags`,
		"review state": `SELECT count(*) FROM expression_review_state`,
	} {
		if got := countDatabaseRows(t, pool, query); got != 0 {
			t.Fatalf("%s rows after direct-publish failure = %d, want 0", table, got)
		}
	}
}

func TestDraftPublishRollsBackContentSourceTagsAndStatusWhenStateInsertFails(t *testing.T) {
	pool := requireIntegration(t)
	clock := &fixedClock{now: time.Date(2026, time.August, 8, 2, 0, 0, 0, time.UTC)}
	service := NewService(pool, clock)
	draft, err := service.CreateExpression(t.Context(), CreateExpressionInput{
		Expression: "Draft publish rollback",
		MeaningKo:  "original meaning",
		Tags:       []string{"original"},
	})
	if err != nil {
		t.Fatalf("create draft fixture: %v", err)
	}
	installTemporaryDatabaseObjects(t, `
		CREATE FUNCTION fail_publish_state_insert() RETURNS trigger LANGUAGE plpgsql AS $$
		BEGIN
			RAISE EXCEPTION 'injected publish state failure';
		END
		$$;
		CREATE TRIGGER fail_publish_state_insert_trigger
		BEFORE INSERT ON expression_review_state
		FOR EACH ROW EXECUTE FUNCTION fail_publish_state_insert();
	`, `
		DROP TRIGGER IF EXISTS fail_publish_state_insert_trigger ON expression_review_state;
		DROP FUNCTION IF EXISTS fail_publish_state_insert();
	`)

	clock.Set(time.Date(2026, time.August, 9, 3, 0, 0, 0, time.UTC))
	changedMeaning := "changed meaning"
	changedSource := "https://example.com/publish"
	changedTags := []string{"changed"}
	published := StatusPublished
	_, err = service.UpdateExpression(t.Context(), draft.ID, UpdateExpressionInput{
		MeaningKo: &changedMeaning,
		SourceURL: OptionalString{Set: true, Value: &changedSource},
		Tags:      &changedTags,
		Status:    &published,
	})
	if err == nil {
		t.Fatal("UpdateExpression() error = nil, want injected state failure")
	}

	var (
		meaning        string
		status         string
		source         string
		hasPublishedAt bool
	)
	if err := pool.QueryRow(t.Context(), `
		SELECT meaning_ko, status, coalesce(source_url, ''), published_at IS NOT NULL
		FROM expressions
		WHERE id = $1
	`, draft.ID).Scan(&meaning, &status, &source, &hasPublishedAt); err != nil {
		t.Fatalf("read draft after failed publish: %v", err)
	}
	if meaning != "original meaning" || status != "draft" || source != "" || hasPublishedAt {
		t.Fatalf("draft after failed publish = meaning %q status %q source %q publishedAt=%v", meaning, status, source, hasPublishedAt)
	}
	rows, err := pool.Query(t.Context(), `SELECT tag FROM expression_tags WHERE expression_id = $1 ORDER BY tag`, draft.ID)
	if err != nil {
		t.Fatalf("read tags after failed publish: %v", err)
	}
	var tags []string
	for rows.Next() {
		var tag string
		if err := rows.Scan(&tag); err != nil {
			rows.Close()
			t.Fatalf("scan tag after failed publish: %v", err)
		}
		tags = append(tags, tag)
	}
	if err := rows.Err(); err != nil {
		rows.Close()
		t.Fatalf("iterate tags after failed publish: %v", err)
	}
	rows.Close()
	if !reflect.DeepEqual(tags, []string{"original"}) {
		t.Fatalf("tags after failed publish = %v, want [original]", tags)
	}
	if got := countDatabaseRows(t, pool, `SELECT count(*) FROM expression_review_state WHERE expression_id = $1`, draft.ID); got != 0 {
		t.Fatalf("review state rows after failed publish = %d, want 0", got)
	}
}

func TestRecordReviewRollsBackEventWhenReviewStateUpdateFails(t *testing.T) {
	pool := requireIntegration(t)
	clock := &fixedClock{now: time.Date(2026, time.August, 7, 0, 0, 0, 0, time.UTC)}
	service := NewService(pool, clock)
	expression := createPublishedExpression(t, service, CreateExpressionInput{
		Expression: "Review state rollback",
		MeaningKo:  "transaction rollback",
	})
	initial := *expression.ReviewState
	installTemporaryDatabaseObjects(t, `
		CREATE FUNCTION fail_review_state_update() RETURNS trigger LANGUAGE plpgsql AS $$
		BEGIN
			RAISE EXCEPTION 'injected review state update failure';
		END
		$$;
		CREATE TRIGGER fail_review_state_update_trigger
		BEFORE UPDATE ON expression_review_state
		FOR EACH ROW EXECUTE FUNCTION fail_review_state_update();
	`, `
		DROP TRIGGER IF EXISTS fail_review_state_update_trigger ON expression_review_state;
		DROP FUNCTION IF EXISTS fail_review_state_update();
	`)

	clock.Set(time.Date(2026, time.August, 8, 1, 0, 0, 0, time.UTC))
	if _, err := service.RecordReview(t.Context(), expression.ID, review.RatingGood); err == nil {
		t.Fatal("RecordReview() error = nil, want injected review-state update failure")
	}
	if got := countDatabaseRows(t, pool, `SELECT count(*) FROM review_events WHERE expression_id = $1`, expression.ID); got != 0 {
		t.Fatalf("review event rows after state failure = %d, want 0", got)
	}

	var (
		interval  int16
		dueAt     time.Time
		lastAt    *time.Time
		updatedAt time.Time
	)
	if err := pool.QueryRow(t.Context(), `
		SELECT interval_days, due_at, last_reviewed_at, updated_at
		FROM expression_review_state
		WHERE expression_id = $1
	`, expression.ID).Scan(&interval, &dueAt, &lastAt, &updatedAt); err != nil {
		t.Fatalf("read review state after injected failure: %v", err)
	}
	if interval != initial.IntervalDays || !dueAt.Equal(initial.DueAt) || lastAt != nil || !updatedAt.Equal(initial.UpdatedAt) {
		t.Fatalf("state after review failure = interval %d due %s last %v updated %s, want initial %+v", interval, dueAt, lastAt, updatedAt, initial)
	}
}

func countDatabaseRows(t *testing.T, pool *pgxpool.Pool, query string, arguments ...any) int {
	t.Helper()
	var count int
	if err := pool.QueryRow(t.Context(), query, arguments...).Scan(&count); err != nil {
		t.Fatalf("count PostgreSQL rows: %v", err)
	}
	return count
}
