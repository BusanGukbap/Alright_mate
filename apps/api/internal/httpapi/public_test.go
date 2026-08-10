package httpapi

import (
	"errors"
	"net/http"
	"strings"
	"testing"
	"time"

	"alrightmate/api/internal/application"
	"github.com/google/uuid"
)

func TestPublicOperationsRequireNoIdentityAndUseGeneratedShapes(t *testing.T) {
	id := uuid.MustParse("2bd17d88-9406-4caf-aed3-c8e07c102068")
	publishedAt := time.Date(2026, 8, 8, 1, 2, 3, 0, time.UTC)
	note := "Used to greet a friend."
	stub := &stubApplication{
		listPublicResult: application.PublicExpressionPage{
			Items: []application.PublicExpressionListItem{{
				ID:          id,
				Expression:  "Alright, mate?",
				MeaningKo:   "잘 지내?",
				Tags:        []string{"greeting"},
				SourceURL:   "https://example.com/source",
				PublishedAt: publishedAt,
			}},
			Page:     2,
			PageSize: 10,
			Total:    1,
		},
		getPublicResult: application.PublicExpressionDetail{
			ID:          id,
			Expression:  "Alright, mate?",
			MeaningKo:   "잘 지내?",
			Note:        &note,
			Tags:        []string{"greeting"},
			SourceURL:   "https://example.com/source",
			PublishedAt: publishedAt,
		},
	}
	handler := newTestHandler(t, stub, &stubReadiness{})

	listResponse := serve(t, handler, http.MethodGet, "/api/v1/expressions?q=mate&tag=greeting&page=2&pageSize=10", "", nil)
	if listResponse.Code != http.StatusOK {
		t.Fatalf("list status = %d, want 200; body = %s", listResponse.Code, listResponse.Body.String())
	}
	if len(stub.listPublicInputs) != 1 {
		t.Fatalf("ListPublicExpressions calls = %d, want 1", len(stub.listPublicInputs))
	}
	if got := stub.listPublicInputs[0]; got != (application.ListPublicExpressionsInput{Page: 2, PageSize: 10, Query: "mate", Tag: "greeting"}) {
		t.Fatalf("list input = %#v", got)
	}
	assertJSONBody(t, listResponse, map[string]any{
		"items": []any{map[string]any{
			"id":          id.String(),
			"expression":  "Alright, mate?",
			"meaningKo":   "잘 지내?",
			"tags":        []string{"greeting"},
			"sourceUrl":   "https://example.com/source",
			"publishedAt": "2026-08-08T01:02:03Z",
		}},
		"page": 2, "pageSize": 10, "total": 1,
	})
	if strings.Contains(listResponse.Body.String(), "note") || strings.Contains(listResponse.Body.String(), "reviewState") {
		t.Fatalf("public list leaked detail/review fields: %s", listResponse.Body.String())
	}

	detailResponse := serve(t, handler, http.MethodGet, "/api/v1/expressions/"+id.String(), "", nil)
	if detailResponse.Code != http.StatusOK {
		t.Fatalf("detail status = %d, want 200; body = %s", detailResponse.Code, detailResponse.Body.String())
	}
	if len(stub.getPublicIDs) != 1 || stub.getPublicIDs[0] != id {
		t.Fatalf("GetPublicExpression IDs = %#v, want %s", stub.getPublicIDs, id)
	}
	assertJSONBody(t, detailResponse, map[string]any{
		"id": id.String(), "expression": "Alright, mate?", "meaningKo": "잘 지내?", "note": note,
		"tags": []string{"greeting"}, "sourceUrl": "https://example.com/source", "publishedAt": "2026-08-08T01:02:03Z",
	})
	if strings.Contains(detailResponse.Body.String(), "reviewState") {
		t.Fatalf("public detail leaked review fields: %s", detailResponse.Body.String())
	}
}

func TestPublicListUsesContractDefaults(t *testing.T) {
	stub := &stubApplication{listPublicResult: application.PublicExpressionPage{Items: []application.PublicExpressionListItem{}, Page: 1, PageSize: 20}}
	handler := newTestHandler(t, stub, &stubReadiness{})

	response := serve(t, handler, http.MethodGet, "/api/v1/expressions", "", nil)

	if response.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200; body = %s", response.Code, response.Body.String())
	}
	if len(stub.listPublicInputs) != 1 || stub.listPublicInputs[0].Page != 1 || stub.listPublicInputs[0].PageSize != 20 {
		t.Fatalf("list inputs = %#v, want page 1 and pageSize 20", stub.listPublicInputs)
	}
}

func TestPublicRequestValidationReturnsProblem(t *testing.T) {
	tests := []string{
		"/api/v1/expressions/not-a-uuid",
		"/api/v1/expressions?page=0",
		"/api/v1/expressions?pageSize=101",
		"/api/v1/expressions?tag=Not_A_Tag",
		"/api/v1/expressions?q=" + strings.Repeat("x", 201),
	}
	for _, target := range tests {
		t.Run(target, func(t *testing.T) {
			handler := newTestHandler(t, &stubApplication{}, &stubReadiness{})
			response := serve(t, handler, http.MethodGet, target, "", nil)
			assertProblem(t, response, http.StatusBadRequest, "urn:alright-mate:problem:bad-request", "Bad request", strings.Split(target, "?")[0])
		})
	}
}

func TestPublicApplicationErrorsMapWithoutLeakage(t *testing.T) {
	id := uuid.MustParse("2bd17d88-9406-4caf-aed3-c8e07c102068")
	t.Run("not found", func(t *testing.T) {
		handler := newTestHandler(t, &stubApplication{getPublicErr: application.ErrNotFound}, &stubReadiness{})
		response := serve(t, handler, http.MethodGet, "/api/v1/expressions/"+id.String(), "", nil)
		assertProblem(t, response, http.StatusNotFound, "urn:alright-mate:problem:not-found", "Not found", "/api/v1/expressions/"+id.String())
	})

	t.Run("unexpected", func(t *testing.T) {
		handler := newTestHandler(t, &stubApplication{getPublicErr: errors.New("SELECT failed: password=secret")}, &stubReadiness{})
		response := serve(t, handler, http.MethodGet, "/api/v1/expressions/"+id.String(), "", nil)
		assertProblem(t, response, http.StatusInternalServerError, "urn:alright-mate:problem:internal-server-error", "Internal server error", "/api/v1/expressions/"+id.String())
		if strings.Contains(response.Body.String(), "SELECT") || strings.Contains(response.Body.String(), "secret") {
			t.Fatalf("unexpected error leaked: %s", response.Body.String())
		}
	})
}
