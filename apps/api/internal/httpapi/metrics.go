package httpapi

import (
	"net/http"
	"strconv"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/prometheus/client_golang/prometheus"
)

type applicationMetrics struct {
	registry        *prometheus.Registry
	httpRequests    *prometheus.CounterVec
	httpDuration    *prometheus.HistogramVec
	databaseReady   prometheus.Gauge
	dueReviews      prometheus.Gauge
	reviewEvents    prometheus.Counter
	applicationInfo *prometheus.GaugeVec
}

func newApplicationMetrics(buildVersion string) *applicationMetrics {
	if buildVersion == "" {
		buildVersion = "unknown"
	}
	metrics := &applicationMetrics{
		registry: prometheus.NewRegistry(),
		httpRequests: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "alright_mate_http_requests_total",
			Help: "Total HTTP requests handled by method, normalised route and response status.",
		}, []string{"method", "route", "status"}),
		httpDuration: prometheus.NewHistogramVec(prometheus.HistogramOpts{
			Name:    "alright_mate_http_request_duration_seconds",
			Help:    "HTTP request duration in seconds by method, normalised route and response status.",
			Buckets: prometheus.DefBuckets,
		}, []string{"method", "route", "status"}),
		databaseReady: prometheus.NewGauge(prometheus.GaugeOpts{
			Name: "alright_mate_database_ready",
			Help: "Whether the latest PostgreSQL readiness check succeeded.",
		}),
		dueReviews: prometheus.NewGauge(prometheus.GaugeOpts{
			Name: "alright_mate_due_reviews",
			Help: "Number of due reviews returned by the latest successful due-queue request.",
		}),
		reviewEvents: prometheus.NewCounter(prometheus.CounterOpts{
			Name: "alright_mate_review_events_total",
			Help: "Total review events recorded successfully through this process.",
		}),
		applicationInfo: prometheus.NewGaugeVec(prometheus.GaugeOpts{
			Name: "alright_mate_build_info",
			Help: "Build information for the running API process.",
		}, []string{"version"}),
	}
	metrics.registry.MustRegister(
		metrics.httpRequests,
		metrics.httpDuration,
		metrics.databaseReady,
		metrics.dueReviews,
		metrics.reviewEvents,
		metrics.applicationInfo,
	)
	metrics.applicationInfo.WithLabelValues(buildVersion).Set(1)
	return metrics
}

func (metrics *applicationMetrics) middleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		startedAt := time.Now()
		recorder := &statusRecorder{ResponseWriter: writer}
		next.ServeHTTP(recorder, request)
		status := recorder.status
		if status == 0 {
			status = http.StatusOK
		}
		route := chi.RouteContext(request.Context()).RoutePattern()
		if route == "" {
			route = "unmatched"
		}
		labels := []string{normalisedMethod(request.Method), route, strconv.Itoa(status)}
		metrics.httpRequests.WithLabelValues(labels...).Inc()
		metrics.httpDuration.WithLabelValues(labels...).Observe(time.Since(startedAt).Seconds())
	})
}

func normalisedMethod(method string) string {
	switch method {
	case http.MethodGet, http.MethodHead, http.MethodPost, http.MethodPut, http.MethodPatch,
		http.MethodDelete, http.MethodConnect, http.MethodOptions, http.MethodTrace:
		return method
	default:
		return "OTHER"
	}
}

type statusRecorder struct {
	http.ResponseWriter
	status int
}

func (recorder *statusRecorder) WriteHeader(status int) {
	if recorder.status != 0 {
		return
	}
	recorder.status = status
	recorder.ResponseWriter.WriteHeader(status)
}

func (recorder *statusRecorder) Write(body []byte) (int, error) {
	if recorder.status == 0 {
		recorder.WriteHeader(http.StatusOK)
	}
	return recorder.ResponseWriter.Write(body)
}
