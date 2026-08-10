package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"alrightmate/api/internal/application"
	"alrightmate/api/internal/review"
	"github.com/google/uuid"
)

const (
	testOwnerLogin  = "octocat"
	testOwnerHeader = "X-Alright-Mate-Owner"
)

func TestHealthAndReadiness(t *testing.T) {
	t.Run("liveness does not touch PostgreSQL", func(t *testing.T) {
		readiness := &stubReadiness{err: errors.New("PostgreSQL is unavailable: password=secret")}
		handler := newTestHandler(t, &stubApplication{}, readiness)

		response := serve(t, handler, http.MethodGet, "/healthz", "", nil)

		if response.Code != http.StatusOK {
			t.Fatalf("status = %d, want %d; body = %s", response.Code, http.StatusOK, response.Body.String())
		}
		if readiness.calls != 0 {
			t.Fatalf("readiness calls = %d, want 0", readiness.calls)
		}
		assertJSONBody(t, response, map[string]any{"status": "ok"})
	})

	t.Run("readiness succeeds after a bounded ping", func(t *testing.T) {
		readiness := &stubReadiness{}
		handler := newTestHandler(t, &stubApplication{}, readiness)

		startedAt := time.Now()
		response := serve(t, handler, http.MethodGet, "/readyz", "", nil)
		finishedAt := time.Now()

		if response.Code != http.StatusOK {
			t.Fatalf("status = %d, want %d; body = %s", response.Code, http.StatusOK, response.Body.String())
		}
		if readiness.calls != 1 {
			t.Fatalf("readiness calls = %d, want 1", readiness.calls)
		}
		if !readiness.hadDeadline {
			t.Fatal("readiness ping context had no deadline")
		}
		if readiness.deadline.Before(startedAt.Add(40*time.Millisecond)) || readiness.deadline.After(finishedAt.Add(60*time.Millisecond)) {
			t.Fatalf("readiness deadline = %s, want configured 50ms bound between %s and %s", readiness.deadline, startedAt.Add(40*time.Millisecond), finishedAt.Add(60*time.Millisecond))
		}
		assertJSONBody(t, response, map[string]any{"status": "ok"})
	})

	t.Run("readiness failure is a non-sensitive RFC 9457 problem", func(t *testing.T) {
		readiness := &stubReadiness{err: errors.New("PostgreSQL is unavailable: password=secret")}
		handler := newTestHandler(t, &stubApplication{}, readiness)

		response := serve(t, handler, http.MethodGet, "/readyz", "", nil)

		assertProblem(t, response, http.StatusServiceUnavailable, "urn:alright-mate:problem:service-unavailable", "Service unavailable", "/readyz")
		body := response.Body.String()
		for _, secret := range []string{"PostgreSQL", "password", "secret"} {
			if strings.Contains(body, secret) {
				t.Fatalf("problem body leaks %q: %s", secret, body)
			}
		}
	})
}

func TestOwnerIdentityBoundary(t *testing.T) {
	tests := []struct {
		name       string
		headers    map[string]string
		wantStatus int
		wantType   string
		wantTitle  string
		wantCalls  int
	}{
		{
			name:       "missing identity",
			wantStatus: http.StatusUnauthorized,
			wantType:   "urn:alright-mate:problem:unauthorised",
			wantTitle:  "Unauthorised",
		},
		{
			name:       "present non-owner identity",
			headers:    map[string]string{testOwnerHeader: "someone-else"},
			wantStatus: http.StatusForbidden,
			wantType:   "urn:alright-mate:problem:forbidden",
			wantTitle:  "Forbidden",
		},
		{
			name:       "unrecognised caller identity header",
			headers:    map[string]string{"X-Forwarded-User": testOwnerLogin},
			wantStatus: http.StatusUnauthorized,
			wantType:   "urn:alright-mate:problem:unauthorised",
			wantTitle:  "Unauthorised",
		},
		{
			name:       "matching trusted identity",
			headers:    map[string]string{testOwnerHeader: testOwnerLogin},
			wantStatus: http.StatusOK,
			wantCalls:  1,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			application := &stubApplication{}
			handler := newTestHandler(t, application, &stubReadiness{})

			response := serve(t, handler, http.MethodGet, "/api/v1/admin/reviews/due", "", test.headers)

			if response.Code != test.wantStatus {
				t.Fatalf("status = %d, want %d; body = %s", response.Code, test.wantStatus, response.Body.String())
			}
			if application.listDueCalls != test.wantCalls {
				t.Fatalf("ListDueReviews calls = %d, want %d", application.listDueCalls, test.wantCalls)
			}
			if test.wantType != "" {
				assertProblem(t, response, test.wantStatus, test.wantType, test.wantTitle, "/api/v1/admin/reviews/due")
			} else {
				assertJSONBody(t, response, map[string]any{"items": []any{}})
			}
		})
	}
}

func newTestHandler(t *testing.T, application Application, readiness ReadinessChecker) http.Handler {
	t.Helper()
	handler, err := NewHandler(application, readiness, Config{
		OwnerLogin:         testOwnerLogin,
		TrustedOwnerHeader: testOwnerHeader,
		ReadinessTimeout:   50 * time.Millisecond,
		BuildVersion:       "test",
	})
	if err != nil {
		t.Fatalf("NewHandler: %v", err)
	}
	return handler
}

func serve(t *testing.T, handler http.Handler, method, target, body string, headers map[string]string) *httptest.ResponseRecorder {
	t.Helper()
	request := httptest.NewRequest(method, target, strings.NewReader(body))
	for name, value := range headers {
		request.Header.Set(name, value)
	}
	if body != "" {
		request.Header.Set("Content-Type", "application/json")
	}
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	return response
}

func assertJSONBody(t *testing.T, response *httptest.ResponseRecorder, want map[string]any) {
	t.Helper()
	if contentType := response.Header().Get("Content-Type"); contentType != "application/json" {
		t.Fatalf("Content-Type = %q, want application/json", contentType)
	}
	var got map[string]any
	if err := json.Unmarshal(response.Body.Bytes(), &got); err != nil {
		t.Fatalf("decode JSON: %v; body = %s", err, response.Body.String())
	}
	wantJSON, err := json.Marshal(want)
	if err != nil {
		t.Fatalf("encode expected JSON: %v", err)
	}
	var normalisedWant map[string]any
	if err := json.Unmarshal(wantJSON, &normalisedWant); err != nil {
		t.Fatalf("normalise expected JSON: %v", err)
	}
	if !mapsEqual(got, normalisedWant) {
		t.Fatalf("JSON body = %#v, want %#v", got, normalisedWant)
	}
}

func assertProblem(t *testing.T, response *httptest.ResponseRecorder, status int, problemType, title, instance string) {
	t.Helper()
	if contentType := response.Header().Get("Content-Type"); contentType != "application/problem+json" {
		t.Fatalf("Content-Type = %q, want application/problem+json; body = %s", contentType, response.Body.String())
	}
	var problem map[string]any
	if err := json.Unmarshal(response.Body.Bytes(), &problem); err != nil {
		t.Fatalf("decode problem JSON: %v; body = %s", err, response.Body.String())
	}
	if got := problem["type"]; got != problemType {
		t.Errorf("problem type = %#v, want %q", got, problemType)
	}
	if got := problem["title"]; got != title {
		t.Errorf("problem title = %#v, want %q", got, title)
	}
	if got := problem["status"]; got != float64(status) {
		t.Errorf("problem status = %#v, want %d", got, status)
	}
	if got := problem["instance"]; got != instance {
		t.Errorf("problem instance = %#v, want %q", got, instance)
	}
	if detail, ok := problem["detail"].(string); !ok || detail == "" {
		t.Errorf("problem detail = %#v, want non-empty string", problem["detail"])
	}
}

func mapsEqual(left, right map[string]any) bool {
	leftJSON, leftErr := json.Marshal(left)
	rightJSON, rightErr := json.Marshal(right)
	return leftErr == nil && rightErr == nil && string(leftJSON) == string(rightJSON)
}

type stubReadiness struct {
	err         error
	calls       int
	hadDeadline bool
	deadline    time.Time
}

func (readiness *stubReadiness) Ping(ctx context.Context) error {
	readiness.calls++
	readiness.deadline, readiness.hadDeadline = ctx.Deadline()
	return readiness.err
}

type stubApplication struct {
	listPublicInputs []application.ListPublicExpressionsInput
	listPublicResult application.PublicExpressionPage
	listPublicErr    error
	getPublicIDs     []uuid.UUID
	getPublicResult  application.PublicExpressionDetail
	getPublicErr     error
	createInputs     []application.CreateExpressionInput
	createResult     application.Expression
	createErr        error
	updateIDs        []uuid.UUID
	updateInputs     []application.UpdateExpressionInput
	updateResult     application.Expression
	updateErr        error
	listDueCalls     int
	listDueLimits    []int32
	listDueResult    []application.DueReview
	listDueErr       error
	reviewIDs        []uuid.UUID
	reviewRatings    []review.Rating
	reviewResult     application.ReviewEventResult
	reviewErr        error
	progressDays     []int32
	progressResult   application.ReviewProgress
	progressErr      error
}

func (stub *stubApplication) ListPublicExpressions(_ context.Context, input application.ListPublicExpressionsInput) (application.PublicExpressionPage, error) {
	stub.listPublicInputs = append(stub.listPublicInputs, input)
	return stub.listPublicResult, stub.listPublicErr
}

func (stub *stubApplication) GetPublicExpression(_ context.Context, id uuid.UUID) (application.PublicExpressionDetail, error) {
	stub.getPublicIDs = append(stub.getPublicIDs, id)
	return stub.getPublicResult, stub.getPublicErr
}

func (stub *stubApplication) CreateExpression(_ context.Context, input application.CreateExpressionInput) (application.Expression, error) {
	stub.createInputs = append(stub.createInputs, input)
	return stub.createResult, stub.createErr
}

func (stub *stubApplication) UpdateExpression(_ context.Context, id uuid.UUID, input application.UpdateExpressionInput) (application.Expression, error) {
	stub.updateIDs = append(stub.updateIDs, id)
	stub.updateInputs = append(stub.updateInputs, input)
	return stub.updateResult, stub.updateErr
}

func (stub *stubApplication) ListDueReviews(_ context.Context, limit int32) ([]application.DueReview, error) {
	stub.listDueCalls++
	stub.listDueLimits = append(stub.listDueLimits, limit)
	if stub.listDueResult == nil {
		stub.listDueResult = []application.DueReview{}
	}
	return stub.listDueResult, stub.listDueErr
}

func (stub *stubApplication) RecordReview(_ context.Context, id uuid.UUID, rating review.Rating) (application.ReviewEventResult, error) {
	stub.reviewIDs = append(stub.reviewIDs, id)
	stub.reviewRatings = append(stub.reviewRatings, rating)
	return stub.reviewResult, stub.reviewErr
}

func (stub *stubApplication) GetReviewProgress(_ context.Context, days int32) (application.ReviewProgress, error) {
	stub.progressDays = append(stub.progressDays, days)
	return stub.progressResult, stub.progressErr
}
