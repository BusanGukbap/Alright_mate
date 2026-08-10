package httpapi

import (
	"net/http"
	"strings"
	"testing"
	"time"

	"alrightmate/api/internal/application"
	"alrightmate/api/internal/review"
	"github.com/google/uuid"
)

func TestMetricsExposeBoundedNormalisedApplicationSignals(t *testing.T) {
	id := uuid.MustParse("2bd17d88-9406-4caf-aed3-c8e07c102068")
	eventID := uuid.MustParse("68b914cb-d88a-4321-9af9-75fc60c171ea")
	now := time.Date(2026, 8, 8, 4, 0, 0, 0, time.UTC)
	stub := &stubApplication{
		getPublicErr: application.ErrNotFound,
		listDueResult: []application.DueReview{{
			Expression:  application.DueReviewExpression{ID: id, Expression: "Alright?", MeaningKo: "잘 지내?", Tags: []string{}, SourceURL: "https://example.com", Status: application.StatusPublished, PublishedAt: now},
			ReviewState: application.ReviewState{IntervalDays: 1, DueAt: now, UpdatedAt: now},
		}},
		reviewResult: application.ReviewEventResult{
			EventID: eventID, ExpressionID: id, Rating: review.RatingGood, ReviewedAt: now, ReviewDateSeoul: "2026-08-08",
			PreviousIntervalDays: 1, NextIntervalDays: 3,
			ReviewState: application.ReviewState{IntervalDays: 3, DueAt: now.Add(72 * time.Hour), LastReviewedAt: &now, UpdatedAt: now},
		},
	}
	handler := newTestHandler(t, stub, &stubReadiness{})
	owner := map[string]string{testOwnerHeader: testOwnerLogin}

	serve(t, handler, http.MethodGet, "/healthz", "", nil)
	serve(t, handler, http.MethodGet, "/readyz", "", nil)
	serve(t, handler, http.MethodGet, "/api/v1/expressions?q=raw-secret-value", "", nil)
	serve(t, handler, http.MethodGet, "/api/v1/expressions/"+id.String(), "", nil)
	serve(t, handler, http.MethodGet, "/api/v1/admin/reviews/due", "", owner)
	serve(t, handler, http.MethodPost, "/api/v1/admin/review-events", `{"expressionId":"`+id.String()+`","rating":"good"}`, owner)
	serve(t, handler, "CALLER-CONTROLLED-VERB", "/healthz", "", nil)
	metrics := serve(t, handler, http.MethodGet, "/metrics", "", nil)

	if metrics.Code != http.StatusOK {
		t.Fatalf("metrics status = %d, want 200; body = %s", metrics.Code, metrics.Body.String())
	}
	body := metrics.Body.String()
	for _, expected := range []string{
		`alright_mate_http_requests_total{method="GET",route="/healthz",status="200"} 1`,
		`alright_mate_http_requests_total{method="GET",route="/api/v1/expressions/{id}",status="404"} 1`,
		`alright_mate_http_requests_total{method="OTHER",route="unmatched",status="405"} 1`,
		`alright_mate_http_request_duration_seconds`,
		`alright_mate_database_ready 1`,
		`alright_mate_due_reviews 1`,
		`alright_mate_review_events_total 1`,
		`alright_mate_build_info{version="test"} 1`,
	} {
		if !strings.Contains(body, expected) {
			t.Errorf("metrics missing %q\n%s", expected, body)
		}
	}
	if strings.Contains(body, id.String()) {
		t.Fatalf("metrics leaked raw expression ID: %s", body)
	}
	if strings.Contains(body, "CALLER-CONTROLLED-VERB") {
		t.Fatalf("metrics used an unbounded method label: %s", body)
	}
	for _, rawQueryValue := range []string{"raw-secret-value", "q="} {
		if strings.Contains(body, rawQueryValue) {
			t.Fatalf("metrics leaked raw query data %q: %s", rawQueryValue, body)
		}
	}
}
