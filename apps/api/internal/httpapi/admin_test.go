package httpapi

import (
	"errors"
	"net/http"
	"strings"
	"testing"
	"time"

	"alrightmate/api/internal/application"
	"alrightmate/api/internal/review"
	"github.com/google/uuid"
)

func TestOwnerOperationsMapApplicationInputsAndGeneratedResponses(t *testing.T) {
	id := uuid.MustParse("2bd17d88-9406-4caf-aed3-c8e07c102068")
	eventID := uuid.MustParse("68b914cb-d88a-4321-9af9-75fc60c171ea")
	createdAt := time.Date(2026, 8, 1, 2, 3, 4, 0, time.UTC)
	updatedAt := time.Date(2026, 8, 2, 3, 4, 5, 0, time.UTC)
	publishedAt := time.Date(2026, 8, 2, 3, 0, 0, 0, time.UTC)
	dueAt := time.Date(2026, 8, 9, 3, 0, 0, 0, time.UTC)
	reviewedAt := time.Date(2026, 8, 8, 4, 0, 0, 0, time.UTC)
	note := "Public note"
	sourceURL := "https://example.com/source"
	reviewState := application.ReviewState{IntervalDays: 7, DueAt: dueAt, LastReviewedAt: &reviewedAt, UpdatedAt: reviewedAt}
	stub := &stubApplication{
		createResult: application.Expression{
			ID: id, Expression: "All right?", MeaningKo: "잘 지내?", Tags: []string{"greeting"}, Status: application.StatusDraft,
			CreatedAt: createdAt, UpdatedAt: createdAt,
		},
		updateResult: application.Expression{
			ID: id, Expression: "Alright, mate?", MeaningKo: "잘 지내?", Note: &note, SourceURL: &sourceURL, Tags: []string{"greeting"},
			Status: application.StatusPublished, CreatedAt: createdAt, UpdatedAt: updatedAt, PublishedAt: &publishedAt, ReviewState: &reviewState,
		},
		listDueResult: []application.DueReview{{
			Expression:  application.DueReviewExpression{ID: id, Expression: "Alright, mate?", MeaningKo: "잘 지내?", Note: &note, Tags: []string{"greeting"}, SourceURL: sourceURL, Status: application.StatusPublished, PublishedAt: publishedAt},
			ReviewState: reviewState,
		}},
		reviewResult: application.ReviewEventResult{
			EventID: eventID, ExpressionID: id, Rating: review.RatingGood, ReviewedAt: reviewedAt, ReviewDateSeoul: "2026-08-08",
			PreviousIntervalDays: 3, NextIntervalDays: 7, ReviewState: reviewState,
		},
		progressResult: application.ReviewProgress{
			Days: 2, StartDate: "2026-08-07", EndDate: "2026-08-08", Total: 1,
			Entries: []application.ProgressDay{{Date: "2026-08-07"}, {Date: "2026-08-08", Total: 1, Good: 1}},
		},
	}
	handler := newTestHandler(t, stub, &stubReadiness{})
	owner := map[string]string{testOwnerHeader: testOwnerLogin}

	createResponse := serve(t, handler, http.MethodPost, "/api/v1/admin/expressions", `{"expression":"All right?","meaningKo":"잘 지내?","tags":["greeting"]}`, owner)
	if createResponse.Code != http.StatusCreated {
		t.Fatalf("create status = %d, want 201; body = %s", createResponse.Code, createResponse.Body.String())
	}
	if len(stub.createInputs) != 1 || stub.createInputs[0].Status != application.StatusDraft || stub.createInputs[0].Expression != "All right?" {
		t.Fatalf("create inputs = %#v", stub.createInputs)
	}
	assertJSONBody(t, createResponse, map[string]any{
		"id": id.String(), "expression": "All right?", "meaningKo": "잘 지내?", "tags": []string{"greeting"},
		"status": "draft", "createdAt": "2026-08-01T02:03:04Z", "updatedAt": "2026-08-01T02:03:04Z",
	})

	updateResponse := serve(t, handler, http.MethodPatch, "/api/v1/admin/expressions/"+id.String(), `{"note":null,"sourceUrl":null,"tags":["greeting"],"status":"published"}`, owner)
	if updateResponse.Code != http.StatusOK {
		t.Fatalf("update status = %d, want 200; body = %s", updateResponse.Code, updateResponse.Body.String())
	}
	if len(stub.updateInputs) != 1 || !stub.updateInputs[0].Note.Set || stub.updateInputs[0].Note.Value != nil || !stub.updateInputs[0].SourceURL.Set || stub.updateInputs[0].SourceURL.Value != nil {
		t.Fatalf("nullable update input = %#v", stub.updateInputs)
	}
	if stub.updateInputs[0].Status == nil || *stub.updateInputs[0].Status != application.StatusPublished {
		t.Fatalf("update status input = %#v", stub.updateInputs[0].Status)
	}
	assertJSONBody(t, updateResponse, map[string]any{
		"id": id.String(), "expression": "Alright, mate?", "meaningKo": "잘 지내?", "note": note, "tags": []string{"greeting"},
		"sourceUrl": sourceURL, "status": "published", "createdAt": "2026-08-01T02:03:04Z", "updatedAt": "2026-08-02T03:04:05Z",
		"publishedAt": "2026-08-02T03:00:00Z", "reviewState": reviewStateJSON(reviewState),
	})

	dueResponse := serve(t, handler, http.MethodGet, "/api/v1/admin/reviews/due?limit=33", "", owner)
	if dueResponse.Code != http.StatusOK || len(stub.listDueLimits) != 1 || stub.listDueLimits[0] != 33 {
		t.Fatalf("due status/input = %d/%#v; body = %s", dueResponse.Code, stub.listDueLimits, dueResponse.Body.String())
	}
	assertJSONBody(t, dueResponse, map[string]any{"items": []any{map[string]any{
		"expression": map[string]any{
			"id": id.String(), "expression": "Alright, mate?", "meaningKo": "잘 지내?", "note": note, "tags": []string{"greeting"},
			"sourceUrl": sourceURL, "status": "published", "publishedAt": "2026-08-02T03:00:00Z",
		},
		"reviewState": reviewStateJSON(reviewState),
	}}})

	reviewResponse := serve(t, handler, http.MethodPost, "/api/v1/admin/review-events", `{"expressionId":"`+id.String()+`","rating":"good"}`, owner)
	if reviewResponse.Code != http.StatusCreated || len(stub.reviewRatings) != 1 || stub.reviewRatings[0] != review.RatingGood {
		t.Fatalf("review status/input = %d/%#v; body = %s", reviewResponse.Code, stub.reviewRatings, reviewResponse.Body.String())
	}
	assertJSONBody(t, reviewResponse, map[string]any{
		"eventId": eventID.String(), "expressionId": id.String(), "rating": "good", "reviewedAt": "2026-08-08T04:00:00Z",
		"reviewDateSeoul": "2026-08-08", "previousIntervalDays": 3, "nextIntervalDays": 7, "reviewState": reviewStateJSON(reviewState),
	})

	progressResponse := serve(t, handler, http.MethodGet, "/api/v1/admin/progress?days=2", "", owner)
	if progressResponse.Code != http.StatusOK || len(stub.progressDays) != 1 || stub.progressDays[0] != 2 {
		t.Fatalf("progress status/input = %d/%#v; body = %s", progressResponse.Code, stub.progressDays, progressResponse.Body.String())
	}
	assertJSONBody(t, progressResponse, map[string]any{
		"days": 2, "startDate": "2026-08-07", "endDate": "2026-08-08", "total": 1,
		"entries": []any{map[string]any{"date": "2026-08-07", "total": 0, "again": 0, "good": 0}, map[string]any{"date": "2026-08-08", "total": 1, "again": 0, "good": 1}},
	})
}

func TestOwnerOperationDefaults(t *testing.T) {
	stub := ownerBoundaryStub()
	handler := newTestHandler(t, stub, &stubReadiness{})
	owner := map[string]string{testOwnerHeader: testOwnerLogin}

	dueResponse := serve(t, handler, http.MethodGet, "/api/v1/admin/reviews/due", "", owner)
	if dueResponse.Code != http.StatusOK {
		t.Fatalf("due status = %d, want 200; body = %s", dueResponse.Code, dueResponse.Body.String())
	}
	if len(stub.listDueLimits) != 1 || stub.listDueLimits[0] != 20 {
		t.Fatalf("due default limit = %#v, want [20]", stub.listDueLimits)
	}

	progressResponse := serve(t, handler, http.MethodGet, "/api/v1/admin/progress", "", owner)
	if progressResponse.Code != http.StatusOK {
		t.Fatalf("progress status = %d, want 200; body = %s", progressResponse.Code, progressResponse.Body.String())
	}
	if len(stub.progressDays) != 1 || stub.progressDays[0] != 30 {
		t.Fatalf("progress default days = %#v, want [30]", stub.progressDays)
	}
}

func TestAdminExpressionGeneratedUnionBranches(t *testing.T) {
	id := uuid.MustParse("2bd17d88-9406-4caf-aed3-c8e07c102068")
	createdAt := time.Date(2026, 8, 1, 2, 3, 4, 0, time.UTC)
	updatedAt := time.Date(2026, 8, 2, 3, 4, 5, 0, time.UTC)
	publishedAt := time.Date(2026, 8, 2, 3, 0, 0, 0, time.UTC)
	archivedAt := time.Date(2026, 8, 3, 4, 0, 0, 0, time.UTC)
	dueAt := time.Date(2026, 8, 9, 3, 0, 0, 0, time.UTC)
	sourceURL := "https://example.com/source"
	reviewState := application.ReviewState{IntervalDays: 7, DueAt: dueAt, UpdatedAt: updatedAt}
	owner := map[string]string{testOwnerHeader: testOwnerLogin}

	t.Run("direct-published create", func(t *testing.T) {
		stub := &stubApplication{createResult: application.Expression{
			ID: id, Expression: "Alright?", MeaningKo: "괜찮아?", SourceURL: &sourceURL, Tags: []string{"greeting"},
			Status: application.StatusPublished, CreatedAt: createdAt, UpdatedAt: updatedAt, PublishedAt: &publishedAt, ReviewState: &reviewState,
		}}
		handler := newTestHandler(t, stub, &stubReadiness{})
		response := serve(t, handler, http.MethodPost, "/api/v1/admin/expressions", `{"expression":"Alright?","meaningKo":"괜찮아?","sourceUrl":"https://example.com/source","tags":["greeting"],"status":"published"}`, owner)

		if response.Code != http.StatusCreated {
			t.Fatalf("status = %d, want 201; body = %s", response.Code, response.Body.String())
		}
		if len(stub.createInputs) != 1 || stub.createInputs[0].Status != application.StatusPublished || stub.createInputs[0].SourceURL == nil || *stub.createInputs[0].SourceURL != sourceURL {
			t.Fatalf("create input = %#v", stub.createInputs)
		}
		assertJSONBody(t, response, map[string]any{
			"id": id.String(), "expression": "Alright?", "meaningKo": "괜찮아?", "sourceUrl": sourceURL, "tags": []string{"greeting"},
			"status": "published", "createdAt": "2026-08-01T02:03:04Z", "updatedAt": "2026-08-02T03:04:05Z",
			"publishedAt": "2026-08-02T03:00:00Z", "reviewState": reviewStateJSON(reviewState),
		})
	})

	t.Run("archived update", func(t *testing.T) {
		stub := &stubApplication{updateResult: application.Expression{
			ID: id, Expression: "Alright?", MeaningKo: "괜찮아?", SourceURL: &sourceURL, Tags: []string{"greeting"},
			Status: application.StatusArchived, CreatedAt: createdAt, UpdatedAt: updatedAt, PublishedAt: &publishedAt, ArchivedAt: &archivedAt, ReviewState: &reviewState,
		}}
		handler := newTestHandler(t, stub, &stubReadiness{})
		response := serve(t, handler, http.MethodPatch, "/api/v1/admin/expressions/"+id.String(), `{"status":"archived"}`, owner)

		if response.Code != http.StatusOK {
			t.Fatalf("status = %d, want 200; body = %s", response.Code, response.Body.String())
		}
		if len(stub.updateInputs) != 1 || stub.updateInputs[0].Status == nil || *stub.updateInputs[0].Status != application.StatusArchived {
			t.Fatalf("update input = %#v", stub.updateInputs)
		}
		assertJSONBody(t, response, map[string]any{
			"id": id.String(), "expression": "Alright?", "meaningKo": "괜찮아?", "sourceUrl": sourceURL, "tags": []string{"greeting"},
			"status": "archived", "createdAt": "2026-08-01T02:03:04Z", "updatedAt": "2026-08-02T03:04:05Z",
			"publishedAt": "2026-08-02T03:00:00Z", "archivedAt": "2026-08-03T04:00:00Z", "reviewState": reviewStateJSON(reviewState),
		})
	})
}

func TestOwnerRequestValidationReturnsBadRequestProblem(t *testing.T) {
	id := "2bd17d88-9406-4caf-aed3-c8e07c102068"
	owner := map[string]string{testOwnerHeader: testOwnerLogin}
	tests := []struct {
		method string
		target string
		body   string
	}{
		{http.MethodPost, "/api/v1/admin/expressions", `{"expression":"Hello","meaningKo":"안녕","unknown":true}`},
		{http.MethodPost, "/api/v1/admin/expressions", `{"expression":`},
		{http.MethodPatch, "/api/v1/admin/expressions/" + id, `{}`},
		{http.MethodPatch, "/api/v1/admin/expressions/not-a-uuid", `{"status":"published"}`},
		{http.MethodGet, "/api/v1/admin/reviews/due?limit=0", ""},
		{http.MethodGet, "/api/v1/admin/reviews/due?limit=101", ""},
		{http.MethodPost, "/api/v1/admin/review-events", `{"expressionId":"` + id + `","rating":"maybe"}`},
		{http.MethodGet, "/api/v1/admin/progress?days=0", ""},
		{http.MethodGet, "/api/v1/admin/progress?days=91", ""},
	}

	for _, test := range tests {
		t.Run(test.method+" "+test.target, func(t *testing.T) {
			stub := &stubApplication{}
			handler := newTestHandler(t, stub, &stubReadiness{})
			response := serve(t, handler, test.method, test.target, test.body, owner)
			assertProblem(t, response, http.StatusBadRequest, "urn:alright-mate:problem:bad-request", "Bad request", strings.Split(test.target, "?")[0])
			if len(stub.createInputs)+len(stub.updateInputs)+len(stub.reviewIDs)+len(stub.progressDays)+stub.listDueCalls != 0 {
				t.Fatalf("invalid request reached application: %#v", stub)
			}
		})
	}
}

func TestOwnerApplicationErrorsMapToContractedProblems(t *testing.T) {
	id := uuid.MustParse("2bd17d88-9406-4caf-aed3-c8e07c102068")
	owner := map[string]string{testOwnerHeader: testOwnerLogin}
	tests := []struct {
		name        string
		stub        *stubApplication
		method      string
		target      string
		body        string
		status      int
		problemType string
		title       string
	}{
		{"create conflict", &stubApplication{createErr: application.ErrExpressionConflict}, http.MethodPost, "/api/v1/admin/expressions", `{"expression":"Hello","meaningKo":"안녕"}`, 409, "urn:alright-mate:problem:conflict", "Conflict"},
		{"update not found", &stubApplication{updateErr: application.ErrNotFound}, http.MethodPatch, "/api/v1/admin/expressions/" + id.String(), `{"expression":"Hello"}`, 404, "urn:alright-mate:problem:not-found", "Not found"},
		{"invalid transition", &stubApplication{updateErr: application.ErrInvalidTransition}, http.MethodPatch, "/api/v1/admin/expressions/" + id.String(), `{"status":"archived"}`, 422, "urn:alright-mate:problem:unprocessable-content", "Unprocessable content"},
		{"review duplicate", &stubApplication{reviewErr: application.ErrReviewAlreadyRecorded}, http.MethodPost, "/api/v1/admin/review-events", `{"expressionId":"` + id.String() + `","rating":"good"}`, 409, "urn:alright-mate:problem:review-already-recorded", "Review already recorded"},
		{"review validation", &stubApplication{reviewErr: application.ErrValidation}, http.MethodPost, "/api/v1/admin/review-events", `{"expressionId":"` + id.String() + `","rating":"good"}`, 422, "urn:alright-mate:problem:unprocessable-content", "Unprocessable content"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			handler := newTestHandler(t, test.stub, &stubReadiness{})
			response := serve(t, handler, test.method, test.target, test.body, owner)
			assertProblem(t, response, test.status, test.problemType, test.title, strings.Split(test.target, "?")[0])
		})
	}

	t.Run("unexpected error is non-sensitive", func(t *testing.T) {
		handler := newTestHandler(t, &stubApplication{reviewErr: errors.New("SQL password=secret")}, &stubReadiness{})
		response := serve(t, handler, http.MethodPost, "/api/v1/admin/review-events", `{"expressionId":"`+id.String()+`","rating":"good"}`, owner)
		assertProblem(t, response, 500, "urn:alright-mate:problem:internal-server-error", "Internal server error", "/api/v1/admin/review-events")
		if strings.Contains(response.Body.String(), "SQL") || strings.Contains(response.Body.String(), "secret") {
			t.Fatalf("unexpected error leaked: %s", response.Body.String())
		}
	})
}

func reviewStateJSON(state application.ReviewState) map[string]any {
	result := map[string]any{
		"intervalDays": int(state.IntervalDays), "dueAt": state.DueAt.UTC().Format(time.RFC3339), "updatedAt": state.UpdatedAt.UTC().Format(time.RFC3339),
	}
	if state.LastReviewedAt != nil {
		result["lastReviewedAt"] = state.LastReviewedAt.UTC().Format(time.RFC3339)
	}
	return result
}
