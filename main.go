package main

import (
	"context"
	"crypto/rand"
	"database/sql"
	"embed"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"mime"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"
	"unicode/utf8"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/stdlib"
)

//go:embed web
var web embed.FS

type expression struct {
	ID      int64  `json:"id"`
	Phrase  string `json:"phrase"`
	Meaning string `json:"meaning"`
}

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	if err := run(logger); err != nil {
		logger.Error("server stopped", "error", err.Error())
		os.Exit(1)
	}
}

func run(logger *slog.Logger) error {
	dsn := os.Getenv("DATABASE_URL")
	if dsn == "" {
		return errors.New("set DATABASE_URL to a PostgreSQL connection string")
	}
	config, err := pgx.ParseConfig(dsn)
	if err != nil {
		return errors.New("DATABASE_URL could not be parsed")
	}
	db := stdlib.OpenDB(*config)
	defer db.Close()

	addr := os.Getenv("ADDR")
	if addr == "" {
		addr = "127.0.0.1:8080"
	}
	listener, err := net.Listen("tcp", addr)
	if err != nil {
		return fmt.Errorf("listen: %w", err)
	}
	handler := newHandler(db, logger)
	if listener.Addr().(*net.TCPAddr).IP.IsLoopback() {
		handler = restrictLoopbackHosts(handler)
	}
	server := &http.Server{
		Handler:           handler,
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second,
		WriteTimeout:      10 * time.Second,
		IdleTimeout:       60 * time.Second,
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	serveErr := make(chan error, 1)
	go func() { serveErr <- server.Serve(listener) }()
	logger.Info("server listening", "address", listener.Addr().String())
	select {
	case err := <-serveErr:
		return err
	case <-ctx.Done():
		stop()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		logger.Info("server shutting down")
		return server.Shutdown(shutdownCtx)
	}
}

func newHandler(db *sql.DB, logger *slog.Logger) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /{$}", func(w http.ResponseWriter, r *http.Request) {
		http.ServeFileFS(w, r, web, "web/index.html")
	})
	mux.HandleFunc("GET /app.js", func(w http.ResponseWriter, r *http.Request) {
		http.ServeFileFS(w, r, web, "web/app.js")
	})
	mux.HandleFunc("GET /livez", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]string{"status": "live"})
	})
	mux.HandleFunc("GET /readyz", func(w http.ResponseWriter, r *http.Request) {
		ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
		defer cancel()
		if _, err := db.ExecContext(ctx, "SELECT id, phrase, meaning FROM expressions LIMIT 0"); err != nil {
			databaseUnavailable(w, logger, "readiness", err)
			return
		}
		writeJSON(w, http.StatusOK, map[string]string{"status": "ready"})
	})
	mux.HandleFunc("GET /api/expressions", func(w http.ResponseWriter, r *http.Request) {
		ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
		defer cancel()
		rows, err := db.QueryContext(ctx, "SELECT id, phrase, meaning FROM expressions ORDER BY id DESC")
		if err != nil {
			databaseUnavailable(w, logger, "list expressions", err)
			return
		}
		defer rows.Close()
		items := []expression{}
		for rows.Next() {
			var item expression
			if err := rows.Scan(&item.ID, &item.Phrase, &item.Meaning); err != nil {
				databaseUnavailable(w, logger, "scan expression", err)
				return
			}
			items = append(items, item)
		}
		if err := rows.Err(); err != nil {
			databaseUnavailable(w, logger, "read expressions", err)
			return
		}
		writeJSON(w, http.StatusOK, items)
	})
	mux.HandleFunc("POST /api/expressions", func(w http.ResponseWriter, r *http.Request) {
		mediaType, _, err := mime.ParseMediaType(r.Header.Get("Content-Type"))
		if err != nil || mediaType != "application/json" {
			writeError(w, http.StatusUnsupportedMediaType, "Send the expression as JSON.")
			return
		}
		decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 4096))
		decoder.DisallowUnknownFields()
		var input struct {
			Phrase  string `json:"phrase"`
			Meaning string `json:"meaning"`
		}
		err = decoder.Decode(&input)
		if err == nil {
			err = decoder.Decode(new(json.RawMessage))
			if err == io.EOF {
				err = nil
			} else if err == nil {
				err = errors.New("extra JSON value")
			}
		}
		var tooLarge *http.MaxBytesError
		if errors.As(err, &tooLarge) {
			writeError(w, http.StatusRequestEntityTooLarge, "The request is too large.")
			return
		}
		if err != nil {
			writeError(w, http.StatusBadRequest, "Send one JSON object with phrase and meaning.")
			return
		}
		input.Phrase = strings.TrimSpace(input.Phrase)
		input.Meaning = strings.TrimSpace(input.Meaning)
		if input.Phrase == "" || input.Meaning == "" ||
			utf8.RuneCountInString(input.Phrase) > 120 || utf8.RuneCountInString(input.Meaning) > 500 ||
			strings.ContainsRune(input.Phrase, 0) || strings.ContainsRune(input.Meaning, 0) {
			writeError(w, http.StatusBadRequest, "Enter a phrase of up to 120 characters and a meaning of up to 500 characters.")
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
		defer cancel()
		var item expression
		err = db.QueryRowContext(ctx,
			"INSERT INTO expressions (phrase, meaning) VALUES ($1, $2) RETURNING id, phrase, meaning",
			input.Phrase, input.Meaning).Scan(&item.ID, &item.Phrase, &item.Meaning)
		if err != nil {
			databaseUnavailable(w, logger, "add expression", err)
			return
		}
		writeJSON(w, http.StatusCreated, item)
	})
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		requestID := rand.Text()
		w.Header().Set("X-Request-ID", requestID)
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
		record := &statusResponse{ResponseWriter: w, status: http.StatusOK}
		mux.ServeHTTP(record, r)
		logger.Info("http request",
			"request_id", requestID,
			"method", r.Method,
			"path", r.URL.Path,
			"status", record.status,
			"duration_ms", time.Since(start).Milliseconds(),
		)
	})
}

func restrictLoopbackHosts(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		host, _, err := net.SplitHostPort(r.Host)
		if err != nil {
			host = strings.Trim(r.Host, "[]")
		}
		// A loopback app should not serve hostnames controlled by another site.
		if !strings.EqualFold(host, "localhost") && !net.ParseIP(host).IsLoopback() {
			writeError(w, http.StatusBadRequest, "Use localhost or a loopback IP.")
			return
		}
		next.ServeHTTP(w, r)
	})
}

type statusResponse struct {
	http.ResponseWriter
	status int
}

func (w *statusResponse) WriteHeader(status int) {
	w.status = status
	w.ResponseWriter.WriteHeader(status)
}

func databaseUnavailable(w http.ResponseWriter, logger *slog.Logger, operation string, err error) {
	var pgErr *pgconn.PgError
	sqlState := ""
	if errors.As(err, &pgErr) {
		sqlState = pgErr.Code
	}
	logger.Warn("database operation failed",
		"request_id", w.Header().Get("X-Request-ID"),
		"operation", operation,
		"error_type", fmt.Sprintf("%T", err),
		"sqlstate", sqlState,
	)
	writeError(w, http.StatusServiceUnavailable, "The database is unavailable.")
}

func writeError(w http.ResponseWriter, status int, message string) {
	writeJSON(w, status, map[string]string{"error": message})
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}
