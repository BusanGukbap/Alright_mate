package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"alrightmate/api/internal/application"
	"alrightmate/api/internal/review"
	"github.com/getkin/kin-openapi/openapi3filter"
	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	nethttpmiddleware "github.com/oapi-codegen/nethttp-middleware"
	openapi_types "github.com/oapi-codegen/runtime/types"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

const defaultReadinessTimeout = 2 * time.Second

type Application interface {
	ListPublicExpressions(context.Context, application.ListPublicExpressionsInput) (application.PublicExpressionPage, error)
	GetPublicExpression(context.Context, uuid.UUID) (application.PublicExpressionDetail, error)
	CreateExpression(context.Context, application.CreateExpressionInput) (application.Expression, error)
	UpdateExpression(context.Context, uuid.UUID, application.UpdateExpressionInput) (application.Expression, error)
	ListDueReviews(context.Context, int32) ([]application.DueReview, error)
	RecordReview(context.Context, uuid.UUID, review.Rating) (application.ReviewEventResult, error)
	GetReviewProgress(context.Context, int32) (application.ReviewProgress, error)
}

type ReadinessChecker interface {
	Ping(context.Context) error
}

type Config struct {
	OwnerLogin         string
	TrustedOwnerHeader string
	ReadinessTimeout   time.Duration
	BuildVersion       string
}

type Server struct {
	application Application
	readiness   ReadinessChecker
	config      Config
	metrics     *applicationMetrics
}

var _ StrictServerInterface = (*Server)(nil)

func NewHandler(application Application, readiness ReadinessChecker, config Config) (http.Handler, error) {
	if application == nil {
		return nil, errors.New("application service is required")
	}
	if readiness == nil {
		return nil, errors.New("readiness checker is required")
	}
	if strings.TrimSpace(config.OwnerLogin) == "" {
		return nil, errors.New("owner login is required")
	}
	if strings.TrimSpace(config.TrustedOwnerHeader) == "" {
		return nil, errors.New("trusted owner header is required")
	}
	if config.ReadinessTimeout <= 0 {
		config.ReadinessTimeout = defaultReadinessTimeout
	}

	specification, err := GetSwagger()
	if err != nil {
		return nil, fmt.Errorf("load embedded OpenAPI specification: %w", err)
	}

	metrics := newApplicationMetrics(config.BuildVersion)
	server := &Server{application: application, readiness: readiness, config: config, metrics: metrics}
	strictHandler := NewStrictHandlerWithOptions(
		server,
		[]StrictMiddlewareFunc{requestPathMiddleware},
		StrictHTTPServerOptions{
			RequestErrorHandlerFunc:  writeBadRequest,
			ResponseErrorHandlerFunc: writeInternalError,
		},
	)

	requestValidator := nethttpmiddleware.OapiRequestValidatorWithOptions(specification, &nethttpmiddleware.Options{
		Options: openapi3filter.Options{
			AuthenticationFunc: func(context.Context, *openapi3filter.AuthenticationInput) error {
				return nil
			},
		},
		DoNotValidateServers: true,
		ErrorHandlerWithOpts: func(_ context.Context, _ error, writer http.ResponseWriter, request *http.Request, options nethttpmiddleware.ErrorHandlerOpts) {
			status := options.StatusCode
			if status < 400 || status > 499 {
				status = http.StatusBadRequest
			}
			writeProblem(writer, request.URL.Path, status, problemTypeForStatus(status), problemTitleForStatus(status), "The request does not match the API contract.")
		},
	})

	router := chi.NewRouter()
	router.Use(metrics.middleware)
	router.Use(server.ownerIdentityBoundary)
	router.Use(capturePatchFieldPresence)
	router.NotFound(func(writer http.ResponseWriter, request *http.Request) {
		writeProblem(writer, request.URL.Path, http.StatusNotFound, problemTypeForStatus(http.StatusNotFound), problemTitleForStatus(http.StatusNotFound), "The requested resource was not found.")
	})
	router.MethodNotAllowed(func(writer http.ResponseWriter, request *http.Request) {
		writeProblem(writer, request.URL.Path, http.StatusMethodNotAllowed, problemTypeForStatus(http.StatusMethodNotAllowed), problemTitleForStatus(http.StatusMethodNotAllowed), "The request method is not allowed for this resource.")
	})
	router.Get("/metrics", promhttp.HandlerFor(metrics.registry, promhttp.HandlerOpts{}).ServeHTTP)
	return HandlerWithOptions(strictHandler, ChiServerOptions{
		BaseRouter:  router,
		Middlewares: []MiddlewareFunc{requestValidator},
		ErrorHandlerFunc: func(writer http.ResponseWriter, request *http.Request, _ error) {
			writeBadRequest(writer, request, errors.New("invalid request parameter"))
		},
	}), nil
}

func (server *Server) ownerIdentityBoundary(next http.Handler) http.Handler {
	return http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if !isOwnerOperation(request.Method, request.URL.Path) {
			next.ServeHTTP(writer, request)
			return
		}
		identity := request.Header.Get(server.config.TrustedOwnerHeader)
		switch {
		case identity == "":
			writeProblem(writer, request.URL.Path, http.StatusUnauthorized, "urn:alright-mate:problem:unauthorised", "Unauthorised", "Owner authentication is required.")
		case identity != server.config.OwnerLogin:
			writeProblem(writer, request.URL.Path, http.StatusForbidden, "urn:alright-mate:problem:forbidden", "Forbidden", "The authenticated identity is not the configured owner.")
		default:
			next.ServeHTTP(writer, request)
		}
	})
}

func isOwnerOperation(method, path string) bool {
	switch method {
	case http.MethodPost:
		return path == "/api/v1/admin/expressions" || path == "/api/v1/admin/review-events"
	case http.MethodGet:
		return path == "/api/v1/admin/reviews/due" || path == "/api/v1/admin/progress"
	case http.MethodPatch:
		const prefix = "/api/v1/admin/expressions/"
		segment := strings.TrimPrefix(path, prefix)
		return segment != path && segment != "" && !strings.Contains(segment, "/")
	default:
		return false
	}
}

func (server *Server) GetHealth(context.Context, GetHealthRequestObject) (GetHealthResponseObject, error) {
	return GetHealth200JSONResponse{Status: Ok}, nil
}

func (server *Server) GetReadiness(ctx context.Context, _ GetReadinessRequestObject) (GetReadinessResponseObject, error) {
	pingContext, cancel := context.WithTimeout(ctx, server.config.ReadinessTimeout)
	defer cancel()
	if err := server.readiness.Ping(pingContext); err != nil {
		server.metrics.databaseReady.Set(0)
		problem := newProblem(http.StatusServiceUnavailable, "urn:alright-mate:problem:service-unavailable", "Service unavailable", "A required dependency is unavailable.", requestPath(ctx, "/readyz"))
		return GetReadiness503ApplicationProblemPlusJSONResponse{
			ProblemServiceUnavailableApplicationProblemPlusJSONResponse: ProblemServiceUnavailableApplicationProblemPlusJSONResponse(problem),
		}, nil
	}
	server.metrics.databaseReady.Set(1)
	return GetReadiness200JSONResponse{Status: Ok}, nil
}

func (server *Server) ListDueReviews(ctx context.Context, request ListDueReviewsRequestObject) (ListDueReviewsResponseObject, error) {
	limit := int32(20)
	if request.Params.Limit != nil {
		limit = int32(*request.Params.Limit)
	}
	items, err := server.application.ListDueReviews(ctx, limit)
	if err != nil {
		if errors.Is(err, application.ErrValidation) {
			problem := applicationProblem(http.StatusBadRequest, requestPath(ctx, "/api/v1/admin/reviews/due"))
			return ListDueReviews400ApplicationProblemPlusJSONResponse{
				ProblemBadRequestApplicationProblemPlusJSONResponse: ProblemBadRequestApplicationProblemPlusJSONResponse(problem),
			}, nil
		}
		return nil, err
	}
	responseItems := make([]DueReview, len(items))
	for index, item := range items {
		responseItems[index] = DueReview{
			Expression: DueReviewExpression{
				Id:          item.Expression.ID,
				Expression:  item.Expression.Expression,
				MeaningKo:   item.Expression.MeaningKo,
				Note:        item.Expression.Note,
				Tags:        nonNilTags(item.Expression.Tags),
				SourceUrl:   item.Expression.SourceURL,
				Status:      DueReviewExpressionStatusPublished,
				PublishedAt: item.Expression.PublishedAt.UTC(),
			},
			ReviewState: reviewStateResponse(item.ReviewState),
		}
	}
	server.metrics.dueReviews.Set(float64(len(responseItems)))
	return ListDueReviews200JSONResponse{Items: responseItems}, nil
}

func (server *Server) CreateExpression(ctx context.Context, request CreateExpressionRequestObject) (CreateExpressionResponseObject, error) {
	if request.Body == nil {
		return createBadRequest(ctx), nil
	}

	input := application.CreateExpressionInput{Status: application.StatusDraft}
	published, err := request.Body.AsCreatePublishedExpressionRequest()
	if err == nil && published.Status == CreatePublishedExpressionRequestStatusPublished {
		sourceURL := string(published.SourceUrl)
		input = application.CreateExpressionInput{
			Expression: published.Expression,
			MeaningKo:  published.MeaningKo,
			Note:       published.Note,
			SourceURL:  &sourceURL,
			Tags:       tagsValue(published.Tags),
			Status:     application.StatusPublished,
		}
	} else {
		draft, draftErr := request.Body.AsCreateDraftExpressionRequest()
		if draftErr != nil {
			return createBadRequest(ctx), nil
		}
		input = application.CreateExpressionInput{
			Expression: draft.Expression,
			MeaningKo:  draft.MeaningKo,
			Note:       draft.Note,
			SourceURL:  draft.SourceUrl,
			Tags:       tagsValue(draft.Tags),
			Status:     application.StatusDraft,
		}
	}

	created, err := server.application.CreateExpression(ctx, input)
	if err != nil {
		return createErrorResponse(ctx, err)
	}
	contract, err := adminExpressionResponse(created)
	if err != nil {
		return nil, err
	}
	return CreateExpression201JSONResponse(contract), nil
}

func (server *Server) UpdateExpression(ctx context.Context, request UpdateExpressionRequestObject) (UpdateExpressionResponseObject, error) {
	if request.Body == nil {
		return updateBadRequest(ctx), nil
	}
	input := application.UpdateExpressionInput{
		Expression: request.Body.Expression,
		MeaningKo:  request.Body.MeaningKo,
	}
	fields := patchFields(ctx)
	if _, present := fields["note"]; present {
		input.Note = application.OptionalString{Set: true, Value: request.Body.Note}
	}
	if _, present := fields["sourceUrl"]; present {
		input.SourceURL = application.OptionalString{Set: true, Value: request.Body.SourceUrl}
	}
	if request.Body.Tags != nil {
		tags := []string(*request.Body.Tags)
		input.Tags = &tags
	}
	if request.Body.Status != nil {
		status := application.Status(*request.Body.Status)
		input.Status = &status
	}

	updated, err := server.application.UpdateExpression(ctx, request.Id, input)
	if err != nil {
		return updateErrorResponse(ctx, err)
	}
	contract, err := adminExpressionResponse(updated)
	if err != nil {
		return nil, err
	}
	return UpdateExpression200JSONResponse(contract), nil
}

func (server *Server) GetReviewProgress(ctx context.Context, request GetReviewProgressRequestObject) (GetReviewProgressResponseObject, error) {
	days := int32(30)
	if request.Params.Days != nil {
		days = int32(*request.Params.Days)
	}
	progress, err := server.application.GetReviewProgress(ctx, days)
	if err != nil {
		if errors.Is(err, application.ErrValidation) {
			problem := applicationProblem(http.StatusBadRequest, requestPath(ctx, "/api/v1/admin/progress"))
			return GetReviewProgress400ApplicationProblemPlusJSONResponse{
				ProblemBadRequestApplicationProblemPlusJSONResponse: ProblemBadRequestApplicationProblemPlusJSONResponse(problem),
			}, nil
		}
		return nil, err
	}
	startDate, err := contractDate(progress.StartDate)
	if err != nil {
		return nil, err
	}
	endDate, err := contractDate(progress.EndDate)
	if err != nil {
		return nil, err
	}
	entries := make([]ProgressDay, len(progress.Entries))
	for index, entry := range progress.Entries {
		date, dateErr := contractDate(entry.Date)
		if dateErr != nil {
			return nil, dateErr
		}
		entries[index] = ProgressDay{Date: date, Total: int(entry.Total), Again: int(entry.Again), Good: int(entry.Good)}
	}
	return GetReviewProgress200JSONResponse{
		Days: int(progress.Days), StartDate: startDate, EndDate: endDate, Total: int(progress.Total), Entries: entries,
	}, nil
}

func (server *Server) RecordReviewEvent(ctx context.Context, request RecordReviewEventRequestObject) (RecordReviewEventResponseObject, error) {
	if request.Body == nil {
		return recordReviewBadRequest(ctx), nil
	}
	result, err := server.application.RecordReview(ctx, request.Body.ExpressionId, review.Rating(request.Body.Rating))
	if err != nil {
		return recordReviewErrorResponse(ctx, err)
	}
	reviewDate, err := contractDate(result.ReviewDateSeoul)
	if err != nil {
		return nil, err
	}
	server.metrics.reviewEvents.Inc()
	return RecordReviewEvent201JSONResponse{
		EventId:              result.EventID,
		ExpressionId:         result.ExpressionID,
		Rating:               Rating(result.Rating),
		ReviewedAt:           result.ReviewedAt.UTC(),
		ReviewDateSeoul:      reviewDate,
		PreviousIntervalDays: IntervalDays(result.PreviousIntervalDays),
		NextIntervalDays:     IntervalDays(result.NextIntervalDays),
		ReviewState:          reviewStateResponse(result.ReviewState),
	}, nil
}

func (server *Server) ListPublicExpressions(ctx context.Context, request ListPublicExpressionsRequestObject) (ListPublicExpressionsResponseObject, error) {
	input := application.ListPublicExpressionsInput{Page: 1, PageSize: 20}
	if request.Params.Q != nil {
		input.Query = *request.Params.Q
	}
	if request.Params.Tag != nil {
		input.Tag = *request.Params.Tag
	}
	if request.Params.Page != nil {
		input.Page = int32(*request.Params.Page)
	}
	if request.Params.PageSize != nil {
		input.PageSize = int32(*request.Params.PageSize)
	}

	page, err := server.application.ListPublicExpressions(ctx, input)
	if err != nil {
		if errors.Is(err, application.ErrValidation) {
			problem := applicationProblem(http.StatusBadRequest, requestPath(ctx, "/api/v1/expressions"))
			return ListPublicExpressions400ApplicationProblemPlusJSONResponse{
				ProblemBadRequestApplicationProblemPlusJSONResponse: ProblemBadRequestApplicationProblemPlusJSONResponse(problem),
			}, nil
		}
		return nil, err
	}

	items := make([]PublicExpressionListItem, len(page.Items))
	for index, item := range page.Items {
		items[index] = PublicExpressionListItem{
			Id:          item.ID,
			Expression:  item.Expression,
			MeaningKo:   item.MeaningKo,
			Tags:        nonNilTags(item.Tags),
			SourceUrl:   item.SourceURL,
			PublishedAt: item.PublishedAt.UTC(),
		}
	}
	return ListPublicExpressions200JSONResponse{
		Items:    items,
		Page:     int(page.Page),
		PageSize: int(page.PageSize),
		Total:    int(page.Total),
	}, nil
}

func (server *Server) GetPublicExpression(ctx context.Context, request GetPublicExpressionRequestObject) (GetPublicExpressionResponseObject, error) {
	expression, err := server.application.GetPublicExpression(ctx, request.Id)
	if err != nil {
		switch {
		case errors.Is(err, application.ErrValidation):
			problem := applicationProblem(http.StatusBadRequest, requestPath(ctx, "/api/v1/expressions"))
			return GetPublicExpression400ApplicationProblemPlusJSONResponse{
				ProblemBadRequestApplicationProblemPlusJSONResponse: ProblemBadRequestApplicationProblemPlusJSONResponse(problem),
			}, nil
		case errors.Is(err, application.ErrNotFound):
			problem := applicationProblem(http.StatusNotFound, requestPath(ctx, "/api/v1/expressions"))
			return GetPublicExpression404ApplicationProblemPlusJSONResponse{
				ProblemNotFoundApplicationProblemPlusJSONResponse: ProblemNotFoundApplicationProblemPlusJSONResponse(problem),
			}, nil
		default:
			return nil, err
		}
	}
	return GetPublicExpression200JSONResponse{
		Id:          expression.ID,
		Expression:  expression.Expression,
		MeaningKo:   expression.MeaningKo,
		Note:        expression.Note,
		Tags:        nonNilTags(expression.Tags),
		SourceUrl:   expression.SourceURL,
		PublishedAt: expression.PublishedAt.UTC(),
	}, nil
}

type requestPathKey struct{}
type patchFieldsKey struct{}

func requestPathMiddleware(next StrictHandlerFunc, _ string) StrictHandlerFunc {
	return func(ctx context.Context, writer http.ResponseWriter, request *http.Request, value any) (any, error) {
		ctx = context.WithValue(ctx, requestPathKey{}, request.URL.Path)
		return next(ctx, writer, request, value)
	}
}

func requestPath(ctx context.Context, fallback string) string {
	path, ok := ctx.Value(requestPathKey{}).(string)
	if !ok || path == "" {
		return fallback
	}
	return path
}

func capturePatchFieldPresence(next http.Handler) http.Handler {
	return http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodPatch || request.Body == nil {
			next.ServeHTTP(writer, request)
			return
		}
		limitedBody := http.MaxBytesReader(writer, request.Body, 1<<20)
		body, err := io.ReadAll(limitedBody)
		if err != nil {
			writeBadRequest(writer, request, err)
			return
		}
		request.Body = io.NopCloser(bytes.NewReader(body))
		var fields map[string]json.RawMessage
		if json.Unmarshal(body, &fields) == nil {
			request = request.WithContext(context.WithValue(request.Context(), patchFieldsKey{}, fields))
		}
		next.ServeHTTP(writer, request)
	})
}

func patchFields(ctx context.Context) map[string]json.RawMessage {
	fields, _ := ctx.Value(patchFieldsKey{}).(map[string]json.RawMessage)
	return fields
}

func writeBadRequest(writer http.ResponseWriter, request *http.Request, _ error) {
	writeProblem(writer, request.URL.Path, http.StatusBadRequest, "urn:alright-mate:problem:bad-request", "Bad request", "The request does not match the API contract.")
}

func writeInternalError(writer http.ResponseWriter, request *http.Request, _ error) {
	writeProblem(writer, request.URL.Path, http.StatusInternalServerError, "urn:alright-mate:problem:internal-server-error", "Internal server error", "The server could not complete the request.")
}

func writeProblem(writer http.ResponseWriter, instance string, status int, problemType, title, detail string) {
	writer.Header().Set("Content-Type", "application/problem+json")
	writer.WriteHeader(status)
	_ = json.NewEncoder(writer).Encode(newProblem(status, problemType, title, detail, instance))
}

func newProblem(status int, problemType, title, detail, instance string) Problem {
	return Problem{
		Type:     &problemType,
		Title:    &title,
		Status:   &status,
		Detail:   &detail,
		Instance: &instance,
	}
}

func problemTypeForStatus(status int) string {
	switch status {
	case http.StatusUnauthorized:
		return "urn:alright-mate:problem:unauthorised"
	case http.StatusForbidden:
		return "urn:alright-mate:problem:forbidden"
	case http.StatusNotFound:
		return "urn:alright-mate:problem:not-found"
	case http.StatusMethodNotAllowed:
		return "urn:alright-mate:problem:method-not-allowed"
	default:
		return "urn:alright-mate:problem:bad-request"
	}
}

func problemTitleForStatus(status int) string {
	switch status {
	case http.StatusBadRequest:
		return "Bad request"
	case http.StatusUnauthorized:
		return "Unauthorised"
	case http.StatusForbidden:
		return "Forbidden"
	case http.StatusNotFound:
		return "Not found"
	case http.StatusMethodNotAllowed:
		return "Method not allowed"
	default:
		return http.StatusText(status)
	}
}

func applicationProblem(status int, instance string) Problem {
	switch status {
	case http.StatusBadRequest:
		return newProblem(status, "urn:alright-mate:problem:bad-request", "Bad request", "The request is invalid.", instance)
	case http.StatusNotFound:
		return newProblem(status, "urn:alright-mate:problem:not-found", "Not found", "The requested resource was not found.", instance)
	default:
		return newProblem(status, "urn:alright-mate:problem:internal-server-error", "Internal server error", "The server could not complete the request.", instance)
	}
}

func nonNilTags(tags []string) Tags {
	if tags == nil {
		return Tags{}
	}
	return Tags(tags)
}

func tagsValue(tags *Tags) []string {
	if tags == nil {
		return []string{}
	}
	return append([]string(nil), []string(*tags)...)
}

func reviewStateResponse(state application.ReviewState) ReviewState {
	var lastReviewedAt *time.Time
	if state.LastReviewedAt != nil {
		instant := state.LastReviewedAt.UTC()
		lastReviewedAt = &instant
	}
	return ReviewState{
		IntervalDays:   IntervalDays(state.IntervalDays),
		DueAt:          state.DueAt.UTC(),
		LastReviewedAt: lastReviewedAt,
		UpdatedAt:      state.UpdatedAt.UTC(),
	}
}

func adminExpressionResponse(expression application.Expression) (AdminExpression, error) {
	var result AdminExpression
	switch expression.Status {
	case application.StatusDraft:
		err := result.FromAdminDraftExpression(AdminDraftExpression{
			Id: expression.ID, Expression: expression.Expression, MeaningKo: expression.MeaningKo,
			Note: expression.Note, SourceUrl: expression.SourceURL, Tags: nonNilTags(expression.Tags),
			Status: AdminDraftExpressionStatusDraft, CreatedAt: expression.CreatedAt.UTC(), UpdatedAt: expression.UpdatedAt.UTC(),
		})
		return result, err
	case application.StatusPublished:
		if expression.SourceURL == nil || expression.PublishedAt == nil || expression.ReviewState == nil {
			return AdminExpression{}, errors.New("published application expression is incomplete")
		}
		err := result.FromAdminPublishedExpression(AdminPublishedExpression{
			Id: expression.ID, Expression: expression.Expression, MeaningKo: expression.MeaningKo,
			Note: expression.Note, SourceUrl: *expression.SourceURL, Tags: nonNilTags(expression.Tags),
			Status: AdminPublishedExpressionStatusPublished, CreatedAt: expression.CreatedAt.UTC(), UpdatedAt: expression.UpdatedAt.UTC(),
			PublishedAt: expression.PublishedAt.UTC(), ReviewState: reviewStateResponse(*expression.ReviewState),
		})
		return result, err
	case application.StatusArchived:
		if expression.SourceURL == nil || expression.PublishedAt == nil || expression.ArchivedAt == nil || expression.ReviewState == nil {
			return AdminExpression{}, errors.New("archived application expression is incomplete")
		}
		err := result.FromAdminArchivedExpression(AdminArchivedExpression{
			Id: expression.ID, Expression: expression.Expression, MeaningKo: expression.MeaningKo,
			Note: expression.Note, SourceUrl: *expression.SourceURL, Tags: nonNilTags(expression.Tags),
			Status: AdminArchivedExpressionStatusArchived, CreatedAt: expression.CreatedAt.UTC(), UpdatedAt: expression.UpdatedAt.UTC(),
			PublishedAt: expression.PublishedAt.UTC(), ArchivedAt: expression.ArchivedAt.UTC(), ReviewState: reviewStateResponse(*expression.ReviewState),
		})
		return result, err
	default:
		return AdminExpression{}, fmt.Errorf("unsupported application expression status %q", expression.Status)
	}
}

func contractDate(value string) (openapi_types.Date, error) {
	parsed, err := time.Parse(time.DateOnly, value)
	if err != nil {
		return openapi_types.Date{}, fmt.Errorf("invalid application date: %w", err)
	}
	return openapi_types.Date{Time: parsed}, nil
}

func createBadRequest(ctx context.Context) CreateExpressionResponseObject {
	problem := applicationProblem(http.StatusBadRequest, requestPath(ctx, "/api/v1/admin/expressions"))
	return CreateExpression400ApplicationProblemPlusJSONResponse{
		ProblemBadRequestApplicationProblemPlusJSONResponse: ProblemBadRequestApplicationProblemPlusJSONResponse(problem),
	}
}

func createErrorResponse(ctx context.Context, err error) (CreateExpressionResponseObject, error) {
	instance := requestPath(ctx, "/api/v1/admin/expressions")
	switch {
	case errors.Is(err, application.ErrExpressionConflict):
		problem := conflictProblem(instance)
		return CreateExpression409ApplicationProblemPlusJSONResponse{
			ProblemConflictApplicationProblemPlusJSONResponse: ProblemConflictApplicationProblemPlusJSONResponse(problem),
		}, nil
	case errors.Is(err, application.ErrValidation), errors.Is(err, application.ErrInvalidTransition):
		problem := unprocessableProblem(instance)
		return CreateExpression422ApplicationProblemPlusJSONResponse{
			ProblemUnprocessableApplicationProblemPlusJSONResponse: ProblemUnprocessableApplicationProblemPlusJSONResponse(problem),
		}, nil
	default:
		return nil, err
	}
}

func updateBadRequest(ctx context.Context) UpdateExpressionResponseObject {
	problem := applicationProblem(http.StatusBadRequest, requestPath(ctx, "/api/v1/admin/expressions"))
	return UpdateExpression400ApplicationProblemPlusJSONResponse{
		ProblemBadRequestApplicationProblemPlusJSONResponse: ProblemBadRequestApplicationProblemPlusJSONResponse(problem),
	}
}

func updateErrorResponse(ctx context.Context, err error) (UpdateExpressionResponseObject, error) {
	instance := requestPath(ctx, "/api/v1/admin/expressions")
	switch {
	case errors.Is(err, application.ErrNotFound):
		problem := applicationProblem(http.StatusNotFound, instance)
		return UpdateExpression404ApplicationProblemPlusJSONResponse{
			ProblemNotFoundApplicationProblemPlusJSONResponse: ProblemNotFoundApplicationProblemPlusJSONResponse(problem),
		}, nil
	case errors.Is(err, application.ErrExpressionConflict):
		problem := conflictProblem(instance)
		return UpdateExpression409ApplicationProblemPlusJSONResponse{
			ProblemConflictApplicationProblemPlusJSONResponse: ProblemConflictApplicationProblemPlusJSONResponse(problem),
		}, nil
	case errors.Is(err, application.ErrValidation), errors.Is(err, application.ErrInvalidTransition):
		problem := unprocessableProblem(instance)
		return UpdateExpression422ApplicationProblemPlusJSONResponse{
			ProblemUnprocessableApplicationProblemPlusJSONResponse: ProblemUnprocessableApplicationProblemPlusJSONResponse(problem),
		}, nil
	default:
		return nil, err
	}
}

func recordReviewBadRequest(ctx context.Context) RecordReviewEventResponseObject {
	problem := applicationProblem(http.StatusBadRequest, requestPath(ctx, "/api/v1/admin/review-events"))
	return RecordReviewEvent400ApplicationProblemPlusJSONResponse{
		ProblemBadRequestApplicationProblemPlusJSONResponse: ProblemBadRequestApplicationProblemPlusJSONResponse(problem),
	}
}

func recordReviewErrorResponse(ctx context.Context, err error) (RecordReviewEventResponseObject, error) {
	instance := requestPath(ctx, "/api/v1/admin/review-events")
	switch {
	case errors.Is(err, application.ErrNotFound):
		problem := applicationProblem(http.StatusNotFound, instance)
		return RecordReviewEvent404ApplicationProblemPlusJSONResponse{
			ProblemNotFoundApplicationProblemPlusJSONResponse: ProblemNotFoundApplicationProblemPlusJSONResponse(problem),
		}, nil
	case errors.Is(err, application.ErrReviewAlreadyRecorded):
		problem := newProblem(http.StatusConflict, "urn:alright-mate:problem:review-already-recorded", "Review already recorded", "A review already exists for this expression today in Asia/Seoul.", instance)
		return RecordReviewEvent409ApplicationProblemPlusJSONResponse(problem), nil
	case errors.Is(err, application.ErrValidation), errors.Is(err, application.ErrInvalidTransition):
		problem := unprocessableProblem(instance)
		return RecordReviewEvent422ApplicationProblemPlusJSONResponse{
			ProblemUnprocessableApplicationProblemPlusJSONResponse: ProblemUnprocessableApplicationProblemPlusJSONResponse(problem),
		}, nil
	default:
		return nil, err
	}
}

func conflictProblem(instance string) Problem {
	return newProblem(http.StatusConflict, "urn:alright-mate:problem:conflict", "Conflict", "The request conflicts with existing state.", instance)
}

func unprocessableProblem(instance string) Problem {
	return newProblem(http.StatusUnprocessableEntity, "urn:alright-mate:problem:unprocessable-content", "Unprocessable content", "The request violates a business rule.", instance)
}
