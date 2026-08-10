package application

import (
	"errors"
	"reflect"
	"sort"
	"strings"
	"testing"
	"time"

	"alrightmate/api/internal/review"
	"github.com/google/uuid"
)

func TestPublicExpressionsVisibilityOrderingFilteringAndDetailIsolation(t *testing.T) {
	pool := requireIntegration(t)
	clock := &fixedClock{now: time.Date(2026, time.August, 5, 6, 0, 0, 0, time.UTC)}
	service := NewService(pool, clock)

	older := createPublishedExpression(t, service, CreateExpressionInput{
		Expression: "Mind the gap",
		MeaningKo:  "승강장 틈을 조심하세요",
		Note:       stringPointer("A public explanatory note."),
		Tags:       []string{"travel", "warning"},
	})
	if _, err := service.CreateExpression(t.Context(), CreateExpressionInput{
		Expression: "Hidden draft",
		MeaningKo:  "숨은 초안",
	}); err != nil {
		t.Fatalf("create draft fixture: %v", err)
	}

	clock.Set(time.Date(2026, time.August, 6, 7, 0, 0, 0, time.UTC))
	cheers := createPublishedExpression(t, service, CreateExpressionInput{
		Expression: "Cheers, mate",
		MeaningKo:  "고마움을 전하는 말",
		Tags:       []string{"friendly", "travel"},
	})
	ta := createPublishedExpression(t, service, CreateExpressionInput{
		Expression: "Ta",
		MeaningKo:  "가벼운 고마움",
		Tags:       []string{"friendly"},
	})
	archived := createPublishedExpression(t, service, CreateExpressionInput{
		Expression: "Archived phrase",
		MeaningKo:  "보관된 표현",
		Tags:       []string{"travel"},
	})
	archivedStatus := StatusArchived
	if _, err := service.UpdateExpression(t.Context(), archived.ID, UpdateExpressionInput{Status: &archivedStatus}); err != nil {
		t.Fatalf("archive fixture: %v", err)
	}

	page, err := service.ListPublicExpressions(t.Context(), ListPublicExpressionsInput{Page: 1, PageSize: 2})
	if err != nil {
		t.Fatalf("list public expressions: %v", err)
	}
	if page.Page != 1 || page.PageSize != 2 || page.Total != 3 {
		t.Fatalf("page metadata = page %d size %d total %d, want 1/2/3", page.Page, page.PageSize, page.Total)
	}
	if len(page.Items) != 2 {
		t.Fatalf("page item count = %d, want 2", len(page.Items))
	}
	sameTimeIDs := []string{cheers.ID.String(), ta.ID.String()}
	sort.Sort(sort.Reverse(sort.StringSlice(sameTimeIDs)))
	gotIDs := []string{page.Items[0].ID.String(), page.Items[1].ID.String()}
	if !reflect.DeepEqual(gotIDs, sameTimeIDs) {
		t.Fatalf("same-publication ordering = %v, want id DESC %v", gotIDs, sameTimeIDs)
	}

	secondPage, err := service.ListPublicExpressions(t.Context(), ListPublicExpressionsInput{Page: 2, PageSize: 2})
	if err != nil {
		t.Fatalf("list second public page: %v", err)
	}
	if secondPage.Total != 3 || len(secondPage.Items) != 1 || secondPage.Items[0].ID != older.ID {
		t.Fatalf("second page = total %d items %+v, want older expression only", secondPage.Total, secondPage.Items)
	}

	orderedSameTime := []uuid.UUID{uuid.MustParse(sameTimeIDs[0]), uuid.MustParse(sameTimeIDs[1])}
	assertPublicFilterIDs(t, service, ListPublicExpressionsInput{Page: 1, PageSize: 20, Query: "MIND"}, older.ID)
	assertPublicFilterIDs(t, service, ListPublicExpressionsInput{Page: 1, PageSize: 20, Query: "고마움"}, orderedSameTime...)
	assertPublicFilterIDs(t, service, ListPublicExpressionsInput{Page: 1, PageSize: 20, Tag: " TRAVEL "}, cheers.ID, older.ID)
	assertPublicFilterIDs(t, service, ListPublicExpressionsInput{Page: 1, PageSize: 20, Query: "cheers", Tag: "travel"}, cheers.ID)
	assertPublicFilterIDs(t, service, ListPublicExpressionsInput{Page: 1, PageSize: 20, Query: "   "}, append(orderedSameTime, older.ID)...)

	detail, err := service.GetPublicExpression(t.Context(), older.ID)
	if err != nil {
		t.Fatalf("get public detail: %v", err)
	}
	if detail.ID != older.ID || detail.Note == nil || *detail.Note != "A public explanatory note." {
		t.Fatalf("public detail = %+v, want published expression with public note", detail)
	}
	if detail.SourceURL != "https://example.com/source" {
		t.Fatalf("public detail source URL = %q", detail.SourceURL)
	}

	for name, id := range map[string]uuid.UUID{
		"unknown":  uuid.New(),
		"archived": archived.ID,
	} {
		t.Run(name+" detail is absent", func(t *testing.T) {
			_, err := service.GetPublicExpression(t.Context(), id)
			if !errors.Is(err, ErrNotFound) {
				t.Fatalf("GetPublicExpression() error = %v, want ErrNotFound", err)
			}
		})
	}

	var draftID uuid.UUID
	if err := pool.QueryRow(t.Context(), `SELECT id FROM expressions WHERE expression = 'Hidden draft'`).Scan(&draftID); err != nil {
		t.Fatalf("read draft fixture id: %v", err)
	}
	if _, err := service.GetPublicExpression(t.Context(), draftID); !errors.Is(err, ErrNotFound) {
		t.Fatalf("draft GetPublicExpression() error = %v, want ErrNotFound", err)
	}
}

func TestPublicListValidatesTrimmedQueryLengthAndDistinguishesAbsentTag(t *testing.T) {
	pool := requireIntegration(t)
	service := NewService(pool, &fixedClock{now: time.Date(2026, time.August, 8, 0, 0, 0, 0, time.UTC)})

	for name, input := range map[string]ListPublicExpressionsInput{
		"two hundred runes after trimming": {Page: 1, PageSize: 20, Query: "  " + strings.Repeat("가", 200) + "  "},
		"exactly empty tag is absent":      {Page: 1, PageSize: 20, Tag: ""},
		"valid tag is normalised":          {Page: 1, PageSize: 20, Tag: " TRAVEL "},
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := service.ListPublicExpressions(t.Context(), input); err != nil {
				t.Fatalf("ListPublicExpressions(%+v) error = %v, want nil", input, err)
			}
		})
	}

	for name, input := range map[string]ListPublicExpressionsInput{
		"query exceeds two hundred runes": {Page: 1, PageSize: 20, Query: strings.Repeat("가", 201)},
		"whitespace-only tag is present":  {Page: 1, PageSize: 20, Tag: " \t "},
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := service.ListPublicExpressions(t.Context(), input); !errors.Is(err, ErrValidation) {
				t.Fatalf("ListPublicExpressions(%+v) error = %v, want ErrValidation", input, err)
			}
		})
	}
}

func TestCreateExpressionNormalisesContentAndInitialisesPublishedReviewState(t *testing.T) {
	pool := requireIntegration(t)
	kst := time.FixedZone("KST", 9*60*60)
	clock := &fixedClock{now: time.Date(2026, time.August, 8, 15, 30, 0, 0, kst)}
	service := NewService(pool, clock)

	draft, err := service.CreateExpression(t.Context(), CreateExpressionInput{
		Expression: "  Bob's your uncle  ",
		MeaningKo:  "  아주 간단하다는 뜻  ",
		Tags:       []string{" British-English ", "british-english", "IDIOM"},
	})
	if err != nil {
		t.Fatalf("create draft: %v", err)
	}
	if draft.Expression != "Bob's your uncle" || draft.MeaningKo != "아주 간단하다는 뜻" {
		t.Fatalf("trimmed draft content = %q / %q", draft.Expression, draft.MeaningKo)
	}
	if draft.Status != StatusDraft || draft.SourceURL != nil || draft.PublishedAt != nil || draft.ReviewState != nil {
		t.Fatalf("draft lifecycle = %+v, want source/publication/review absent", draft)
	}
	if !reflect.DeepEqual(draft.Tags, []string{"british-english", "idiom"}) {
		t.Fatalf("normalised tags = %v", draft.Tags)
	}
	if !draft.CreatedAt.Equal(clock.Now().UTC()) || draft.CreatedAt.Location() != time.UTC {
		t.Fatalf("draft createdAt = %s (%s), want UTC %s", draft.CreatedAt, draft.CreatedAt.Location(), clock.Now().UTC())
	}

	publishedStatus := StatusPublished
	published, err := service.CreateExpression(t.Context(), CreateExpressionInput{
		Expression: "Proper job",
		MeaningKo:  "정말 잘했어",
		SourceURL:  stringPointer("https://example.com/source"),
		Status:     publishedStatus,
	})
	if err != nil {
		t.Fatalf("create published expression: %v", err)
	}
	wantPublishedAt := clock.Now().UTC()
	if published.PublishedAt == nil || !published.PublishedAt.Equal(wantPublishedAt) {
		t.Fatalf("publishedAt = %v, want %s", published.PublishedAt, wantPublishedAt)
	}
	if published.ReviewState == nil {
		t.Fatal("published review state = nil")
	}
	if published.ReviewState.IntervalDays != 1 {
		t.Fatalf("initial interval = %d, want 1", published.ReviewState.IntervalDays)
	}
	if wantDue := wantPublishedAt.Add(24 * time.Hour); !published.ReviewState.DueAt.Equal(wantDue) {
		t.Fatalf("initial dueAt = %s, want %s", published.ReviewState.DueAt, wantDue)
	}
}

func TestCreateExpressionRejectsInvalidContentTagsSourcesStatusAndDuplicates(t *testing.T) {
	tests := []struct {
		name  string
		input CreateExpressionInput
		want  error
	}{
		{name: "blank expression", input: CreateExpressionInput{Expression: "  ", MeaningKo: "뜻"}, want: ErrValidation},
		{name: "blank meaning", input: CreateExpressionInput{Expression: "Phrase", MeaningKo: " \t "}, want: ErrValidation},
		{name: "expression too long", input: CreateExpressionInput{Expression: strings.Repeat("x", 301), MeaningKo: "뜻"}, want: ErrValidation},
		{name: "meaning too long", input: CreateExpressionInput{Expression: "Phrase", MeaningKo: strings.Repeat("가", 1001)}, want: ErrValidation},
		{name: "note too long", input: CreateExpressionInput{Expression: "Phrase", MeaningKo: "뜻", Note: stringPointer(strings.Repeat("n", 4001))}, want: ErrValidation},
		{name: "relative source", input: CreateExpressionInput{Expression: "Phrase", MeaningKo: "뜻", SourceURL: stringPointer("/relative")}, want: ErrValidation},
		{name: "non-http source", input: CreateExpressionInput{Expression: "Phrase", MeaningKo: "뜻", SourceURL: stringPointer("ftp://example.com")}, want: ErrValidation},
		{name: "credential source", input: CreateExpressionInput{Expression: "Phrase", MeaningKo: "뜻", SourceURL: stringPointer("https://user:pass@example.com")}, want: ErrValidation},
		{name: "malformed source", input: CreateExpressionInput{Expression: "Phrase", MeaningKo: "뜻", SourceURL: stringPointer("https://example.com/%zz")}, want: ErrValidation},
		{name: "published without source", input: CreateExpressionInput{Expression: "Phrase", MeaningKo: "뜻", Status: StatusPublished}, want: ErrValidation},
		{name: "archived creation", input: CreateExpressionInput{Expression: "Phrase", MeaningKo: "뜻", SourceURL: stringPointer("https://example.com"), Status: StatusArchived}, want: ErrInvalidTransition},
		{name: "invalid tag", input: CreateExpressionInput{Expression: "Phrase", MeaningKo: "뜻", Tags: []string{"bad tag"}}, want: ErrValidation},
		{name: "too many tags", input: CreateExpressionInput{Expression: "Phrase", MeaningKo: "뜻", Tags: twentyOneTags()}, want: ErrValidation},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			pool := requireIntegration(t)
			service := NewService(pool, &fixedClock{now: time.Date(2026, time.August, 8, 0, 0, 0, 0, time.UTC)})
			_, err := service.CreateExpression(t.Context(), test.input)
			if !errors.Is(err, test.want) {
				t.Fatalf("CreateExpression() error = %v, want %v", err, test.want)
			}
		})
	}

	pool := requireIntegration(t)
	service := NewService(pool, &fixedClock{now: time.Date(2026, time.August, 8, 0, 0, 0, 0, time.UTC)})
	if _, err := service.CreateExpression(t.Context(), CreateExpressionInput{Expression: "Fancy that", MeaningKo: "놀라움"}); err != nil {
		t.Fatalf("create canonical fixture: %v", err)
	}
	_, err := service.CreateExpression(t.Context(), CreateExpressionInput{Expression: "  FANCY THAT  ", MeaningKo: "중복"})
	if !errors.Is(err, ErrExpressionConflict) {
		t.Fatalf("canonical duplicate error = %v, want ErrExpressionConflict", err)
	}
}

func TestUpdateExpressionPreservesOmittedFieldsAndEnforcesLifecycle(t *testing.T) {
	pool := requireIntegration(t)
	clock := &fixedClock{now: time.Date(2026, time.August, 1, 1, 0, 0, 0, time.UTC)}
	service := NewService(pool, clock)

	draft, err := service.CreateExpression(t.Context(), CreateExpressionInput{
		Expression: "Have a butcher's",
		MeaningKo:  "한번 보다",
		Note:       stringPointer("Original note"),
		SourceURL:  stringPointer("https://example.com/draft-source"),
		Tags:       []string{"idiom", "london"},
	})
	if err != nil {
		t.Fatalf("create draft fixture: %v", err)
	}

	clock.Set(time.Date(2026, time.August, 2, 2, 0, 0, 0, time.UTC))
	newMeaning := "한번 살펴보다"
	amended, err := service.UpdateExpression(t.Context(), draft.ID, UpdateExpressionInput{MeaningKo: &newMeaning})
	if err != nil {
		t.Fatalf("amend draft: %v", err)
	}
	if amended.Expression != draft.Expression || amended.MeaningKo != newMeaning || amended.Note == nil || *amended.Note != "Original note" || amended.SourceURL == nil || *amended.SourceURL != "https://example.com/draft-source" || !reflect.DeepEqual(amended.Tags, draft.Tags) {
		t.Fatalf("omitted draft fields were lost: before %+v after %+v", draft, amended)
	}

	clear := OptionalString{Set: true}
	cleared, err := service.UpdateExpression(t.Context(), draft.ID, UpdateExpressionInput{Note: clear, SourceURL: clear})
	if err != nil {
		t.Fatalf("clear nullable draft fields: %v", err)
	}
	if cleared.Note != nil || cleared.SourceURL != nil {
		t.Fatalf("cleared fields = note %v source %v, want nil", cleared.Note, cleared.SourceURL)
	}

	clock.Set(time.Date(2026, time.August, 3, 3, 0, 0, 0, time.UTC))
	publishedStatus := StatusPublished
	publishSource := "https://example.com/published-source"
	published, err := service.UpdateExpression(t.Context(), draft.ID, UpdateExpressionInput{
		SourceURL: OptionalString{Set: true, Value: &publishSource},
		Status:    &publishedStatus,
	})
	if err != nil {
		t.Fatalf("publish draft: %v", err)
	}
	if published.Status != StatusPublished || published.PublishedAt == nil || published.ReviewState == nil {
		t.Fatalf("published lifecycle = %+v", published)
	}
	initialState := *published.ReviewState

	clock.Set(time.Date(2026, time.August, 4, 4, 0, 0, 0, time.UTC))
	newExpression := "Have a butcher's at this"
	replacementTags := []string{" UPDATED ", "idiom"}
	updated, err := service.UpdateExpression(t.Context(), draft.ID, UpdateExpressionInput{Expression: &newExpression, Tags: &replacementTags})
	if err != nil {
		t.Fatalf("amend published expression: %v", err)
	}
	if updated.ReviewState == nil || updated.ReviewState.IntervalDays != initialState.IntervalDays || !updated.ReviewState.DueAt.Equal(initialState.DueAt) || !reflect.DeepEqual(updated.Tags, []string{"idiom", "updated"}) {
		t.Fatalf("published amendment changed review state or tags incorrectly: %+v", updated)
	}
	var stateRows int
	if err := pool.QueryRow(t.Context(), `SELECT count(*) FROM expression_review_state WHERE expression_id = $1`, draft.ID).Scan(&stateRows); err != nil {
		t.Fatalf("count review state rows: %v", err)
	}
	if stateRows != 1 {
		t.Fatalf("review state rows = %d, want exactly 1", stateRows)
	}

	reviewed, err := service.RecordReview(t.Context(), draft.ID, review.RatingGood)
	if err != nil {
		t.Fatalf("record review before archive: %v", err)
	}
	clock.Set(reviewed.ReviewState.DueAt.Add(time.Hour))
	archivedStatus := StatusArchived
	archived, err := service.UpdateExpression(t.Context(), draft.ID, UpdateExpressionInput{Status: &archivedStatus})
	if err != nil {
		t.Fatalf("archive published expression: %v", err)
	}
	if archived.ArchivedAt == nil || archived.ReviewState == nil || !archived.ReviewState.DueAt.Equal(reviewed.ReviewState.DueAt) {
		t.Fatalf("archived result did not preserve review state: %+v", archived)
	}
	var eventRows int
	if err := pool.QueryRow(t.Context(), `SELECT count(*) FROM review_events WHERE expression_id = $1`, draft.ID).Scan(&eventRows); err != nil {
		t.Fatalf("count archived review events: %v", err)
	}
	if eventRows != 1 {
		t.Fatalf("archived review event rows = %d, want 1 retained event", eventRows)
	}
	if _, err := service.GetPublicExpression(t.Context(), draft.ID); !errors.Is(err, ErrNotFound) {
		t.Fatalf("archived public detail error = %v, want ErrNotFound", err)
	}
	due, err := service.ListDueReviews(t.Context(), 20)
	if err != nil {
		t.Fatalf("list due reviews after archive: %v", err)
	}
	if len(due) != 0 {
		t.Fatalf("due reviews after archive = %+v, want empty", due)
	}
	archivedMeaning := "보관 후 수정 금지"
	if _, err := service.UpdateExpression(t.Context(), draft.ID, UpdateExpressionInput{MeaningKo: &archivedMeaning}); !errors.Is(err, ErrInvalidTransition) {
		t.Fatalf("archived amendment error = %v, want ErrInvalidTransition", err)
	}
}

func TestUpdateExpressionRejectsEmptyAndInvalidTransitions(t *testing.T) {
	tests := []struct {
		name   string
		setup  Status
		patch  UpdateExpressionInput
		wanted error
	}{
		{name: "empty patch", setup: StatusDraft, patch: UpdateExpressionInput{}, wanted: ErrValidation},
		{name: "draft to archived", setup: StatusDraft, patch: UpdateExpressionInput{Status: statusPointer(StatusArchived)}, wanted: ErrInvalidTransition},
		{name: "published to draft", setup: StatusPublished, patch: UpdateExpressionInput{Status: statusPointer(StatusDraft)}, wanted: ErrInvalidTransition},
		{name: "published to published", setup: StatusPublished, patch: UpdateExpressionInput{Status: statusPointer(StatusPublished)}, wanted: ErrInvalidTransition},
		{name: "draft publish without source", setup: StatusDraft, patch: UpdateExpressionInput{Status: statusPointer(StatusPublished)}, wanted: ErrValidation},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			pool := requireIntegration(t)
			clock := &fixedClock{now: time.Date(2026, time.August, 8, 0, 0, 0, 0, time.UTC)}
			service := NewService(pool, clock)
			input := CreateExpressionInput{Expression: "Transition fixture", MeaningKo: "전환 테스트", Status: test.setup}
			if test.setup == StatusPublished {
				input.SourceURL = stringPointer("https://example.com/source")
			}
			expression, err := service.CreateExpression(t.Context(), input)
			if err != nil {
				t.Fatalf("create transition fixture: %v", err)
			}
			_, err = service.UpdateExpression(t.Context(), expression.ID, test.patch)
			if !errors.Is(err, test.wanted) {
				t.Fatalf("UpdateExpression() error = %v, want %v", err, test.wanted)
			}
		})
	}
}

func TestTagReplacementRollsBackAtomicallyOnDatabaseFailure(t *testing.T) {
	pool := requireIntegration(t)
	service := NewService(pool, &fixedClock{now: time.Date(2026, time.August, 8, 0, 0, 0, 0, time.UTC)})
	expression, err := service.CreateExpression(t.Context(), CreateExpressionInput{
		Expression: "Atomic tags",
		MeaningKo:  "태그 원자성",
		Tags:       []string{"original", "stable"},
	})
	if err != nil {
		t.Fatalf("create tag fixture: %v", err)
	}

	dropTrigger := installTemporaryDatabaseObjects(t, `
		CREATE FUNCTION reject_exploding_tag() RETURNS trigger LANGUAGE plpgsql AS $$
		BEGIN
			IF NEW.tag = 'explode' THEN
				RAISE EXCEPTION 'test rejection';
			END IF;
			RETURN NEW;
		END
		$$;
		CREATE TRIGGER reject_exploding_tag_trigger
		BEFORE INSERT ON expression_tags
		FOR EACH ROW EXECUTE FUNCTION reject_exploding_tag();
	`, `
		DROP TRIGGER IF EXISTS reject_exploding_tag_trigger ON expression_tags;
		DROP FUNCTION IF EXISTS reject_exploding_tag();
	`)

	replacement := []string{"fresh", "explode"}
	if _, err := service.UpdateExpression(t.Context(), expression.ID, UpdateExpressionInput{Tags: &replacement}); err == nil {
		t.Fatal("tag replacement error = nil, want database-trigger failure")
	}
	dropTrigger()

	meaning := "태그 롤백 확인"
	after, err := service.UpdateExpression(t.Context(), expression.ID, UpdateExpressionInput{MeaningKo: &meaning})
	if err != nil {
		t.Fatalf("read expression through follow-up amendment: %v", err)
	}
	if !reflect.DeepEqual(after.Tags, []string{"original", "stable"}) {
		t.Fatalf("tags after failed replacement = %v, want original tags", after.Tags)
	}
}

func assertPublicFilterIDs(t *testing.T, service *Service, input ListPublicExpressionsInput, wanted ...uuid.UUID) {
	t.Helper()
	page, err := service.ListPublicExpressions(t.Context(), input)
	if err != nil {
		t.Fatalf("ListPublicExpressions(%+v): %v", input, err)
	}
	got := make([]uuid.UUID, len(page.Items))
	for index := range page.Items {
		got[index] = page.Items[index].ID
	}
	if !reflect.DeepEqual(got, wanted) || page.Total != int64(len(wanted)) {
		t.Fatalf("ListPublicExpressions(%+v) ids/total = %v/%d, want %v/%d", input, got, page.Total, wanted, len(wanted))
	}
}

func createPublishedExpression(t *testing.T, service *Service, input CreateExpressionInput) Expression {
	t.Helper()
	input.Status = StatusPublished
	if input.SourceURL == nil {
		input.SourceURL = stringPointer("https://example.com/source")
	}
	expression, err := service.CreateExpression(t.Context(), input)
	if err != nil {
		t.Fatalf("create published expression %q: %v", input.Expression, err)
	}
	return expression
}

func statusPointer(status Status) *Status {
	return &status
}

func twentyOneTags() []string {
	tags := make([]string, 21)
	for index := range tags {
		tags[index] = "tag-" + string(rune('a'+index))
	}
	return tags
}
