package httpapi

import (
	"net/http"
	"testing"
	"time"

	"alrightmate/api/internal/application"
	"alrightmate/api/internal/review"
	"github.com/google/uuid"
)

func TestRouterReturnsRFC9457ForUnknownPathsAndWrongMethods(t *testing.T) {
	owner := map[string]string{testOwnerHeader: testOwnerLogin}
	tests := []struct {
		name        string
		method      string
		target      string
		headers     map[string]string
		wantStatus  int
		problemType string
		title       string
	}{
		{"unknown public path", http.MethodGet, "/api/v1/missing", nil, http.StatusNotFound, "urn:alright-mate:problem:not-found", "Not found"},
		{"unknown admin-looking path without identity", http.MethodGet, "/api/v1/admin/missing", nil, http.StatusNotFound, "urn:alright-mate:problem:not-found", "Not found"},
		{"unknown admin-looking path with mismatched identity", http.MethodGet, "/api/v1/admin/missing", map[string]string{testOwnerHeader: "someone-else"}, http.StatusNotFound, "urn:alright-mate:problem:not-found", "Not found"},
		{"unknown admin-looking path with owner identity", http.MethodGet, "/api/v1/admin/missing", owner, http.StatusNotFound, "urn:alright-mate:problem:not-found", "Not found"},
		{"wrong public method", http.MethodPost, "/healthz", nil, http.StatusMethodNotAllowed, "urn:alright-mate:problem:method-not-allowed", "Method not allowed"},
		{"wrong owner method without identity", http.MethodGet, "/api/v1/admin/expressions", nil, http.StatusMethodNotAllowed, "urn:alright-mate:problem:method-not-allowed", "Method not allowed"},
		{"wrong owner method with mismatched identity", http.MethodPost, "/api/v1/admin/reviews/due", map[string]string{testOwnerHeader: "someone-else"}, http.StatusMethodNotAllowed, "urn:alright-mate:problem:method-not-allowed", "Method not allowed"},
		{"wrong owner method with owner identity", http.MethodPost, "/api/v1/admin/progress", owner, http.StatusMethodNotAllowed, "urn:alright-mate:problem:method-not-allowed", "Method not allowed"},
		{"metrics is GET only", http.MethodPost, "/metrics", nil, http.StatusMethodNotAllowed, "urn:alright-mate:problem:method-not-allowed", "Method not allowed"},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			handler := newTestHandler(t, ownerBoundaryStub(), &stubReadiness{})
			response := serve(t, handler, test.method, test.target, "", test.headers)
			assertProblem(t, response, test.wantStatus, test.problemType, test.title, test.target)
		})
	}
}

func TestOwnerIdentityBoundaryCoversEveryKnownOwnerOperation(t *testing.T) {
	id := uuid.MustParse("2bd17d88-9406-4caf-aed3-c8e07c102068")
	operations := []struct {
		name               string
		method             string
		target             string
		body               string
		wantMatchingStatus int
		calls              func(*stubApplication) int
	}{
		{"create expression", http.MethodPost, "/api/v1/admin/expressions", `{"expression":"Hello","meaningKo":"안녕"}`, http.StatusCreated, func(stub *stubApplication) int { return len(stub.createInputs) }},
		{"update expression", http.MethodPatch, "/api/v1/admin/expressions/" + id.String(), `{"expression":"Hello"}`, http.StatusOK, func(stub *stubApplication) int { return len(stub.updateInputs) }},
		{"list due reviews", http.MethodGet, "/api/v1/admin/reviews/due", "", http.StatusOK, func(stub *stubApplication) int { return stub.listDueCalls }},
		{"record review event", http.MethodPost, "/api/v1/admin/review-events", `{"expressionId":"` + id.String() + `","rating":"good"}`, http.StatusCreated, func(stub *stubApplication) int { return len(stub.reviewIDs) }},
		{"get review progress", http.MethodGet, "/api/v1/admin/progress", "", http.StatusOK, func(stub *stubApplication) int { return len(stub.progressDays) }},
	}
	identities := []struct {
		name       string
		headers    map[string]string
		wantStatus int
	}{
		{"missing", nil, http.StatusUnauthorized},
		{"mismatched", map[string]string{testOwnerHeader: "someone-else"}, http.StatusForbidden},
		{"matching", map[string]string{testOwnerHeader: testOwnerLogin}, 0},
	}

	for _, operation := range operations {
		for _, identity := range identities {
			t.Run(operation.name+"/"+identity.name, func(t *testing.T) {
				stub := ownerBoundaryStub()
				handler := newTestHandler(t, stub, &stubReadiness{})
				response := serve(t, handler, operation.method, operation.target, operation.body, identity.headers)
				wantStatus := identity.wantStatus
				if wantStatus == 0 {
					wantStatus = operation.wantMatchingStatus
				}
				if response.Code != wantStatus {
					t.Fatalf("status = %d, want %d; body = %s", response.Code, wantStatus, response.Body.String())
				}
				wantCalls := 0
				if identity.name == "matching" {
					wantCalls = 1
				}
				if got := operation.calls(stub); got != wantCalls {
					t.Fatalf("application calls = %d, want %d", got, wantCalls)
				}
				if wantStatus == http.StatusUnauthorized {
					assertProblem(t, response, wantStatus, "urn:alright-mate:problem:unauthorised", "Unauthorised", operation.target)
				}
				if wantStatus == http.StatusForbidden {
					assertProblem(t, response, wantStatus, "urn:alright-mate:problem:forbidden", "Forbidden", operation.target)
				}
			})
		}
	}
}

func TestMalformedExpressionIDIsAuthenticatedBeforeValidation(t *testing.T) {
	tests := []struct {
		name        string
		headers     map[string]string
		wantStatus  int
		problemType string
		title       string
	}{
		{"missing", nil, http.StatusUnauthorized, "urn:alright-mate:problem:unauthorised", "Unauthorised"},
		{"mismatched", map[string]string{testOwnerHeader: "someone-else"}, http.StatusForbidden, "urn:alright-mate:problem:forbidden", "Forbidden"},
		{"matching", map[string]string{testOwnerHeader: testOwnerLogin}, http.StatusBadRequest, "urn:alright-mate:problem:bad-request", "Bad request"},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			stub := ownerBoundaryStub()
			handler := newTestHandler(t, stub, &stubReadiness{})
			const target = "/api/v1/admin/expressions/not-a-uuid"
			response := serve(t, handler, http.MethodPatch, target, `{"status":"published"}`, test.headers)
			assertProblem(t, response, test.wantStatus, test.problemType, test.title, target)
			if len(stub.updateInputs) != 0 {
				t.Fatalf("invalid request reached application: %#v", stub.updateInputs)
			}
		})
	}
}

func TestOwnerIdentityBoundaryDoesNotAffectPublicOperations(t *testing.T) {
	stub := ownerBoundaryStub()
	handler := newTestHandler(t, stub, &stubReadiness{})
	response := serve(t, handler, http.MethodGet, "/api/v1/expressions", "", map[string]string{testOwnerHeader: "someone-else"})
	if response.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body = %s", response.Code, response.Body.String())
	}
	if len(stub.listPublicInputs) != 1 {
		t.Fatalf("ListPublicExpressions calls = %d, want 1", len(stub.listPublicInputs))
	}
}

func ownerBoundaryStub() *stubApplication {
	id := uuid.MustParse("2bd17d88-9406-4caf-aed3-c8e07c102068")
	eventID := uuid.MustParse("68b914cb-d88a-4321-9af9-75fc60c171ea")
	now := time.Date(2026, 8, 8, 4, 0, 0, 0, time.UTC)
	return &stubApplication{
		listPublicResult: application.PublicExpressionPage{Items: []application.PublicExpressionListItem{}, Page: 1, PageSize: 20},
		createResult:     application.Expression{ID: id, Expression: "Hello", MeaningKo: "안녕", Tags: []string{}, Status: application.StatusDraft, CreatedAt: now, UpdatedAt: now},
		updateResult:     application.Expression{ID: id, Expression: "Hello", MeaningKo: "안녕", Tags: []string{}, Status: application.StatusDraft, CreatedAt: now, UpdatedAt: now},
		listDueResult:    []application.DueReview{},
		reviewResult: application.ReviewEventResult{
			EventID: eventID, ExpressionID: id, Rating: review.RatingGood, ReviewedAt: now, ReviewDateSeoul: "2026-08-08",
			PreviousIntervalDays: 1, NextIntervalDays: 3,
			ReviewState: application.ReviewState{IntervalDays: 3, DueAt: now.Add(72 * time.Hour), LastReviewedAt: &now, UpdatedAt: now},
		},
		progressResult: application.ReviewProgress{
			Days: 30, StartDate: "2026-07-10", EndDate: "2026-08-08", Entries: []application.ProgressDay{},
		},
	}
}
