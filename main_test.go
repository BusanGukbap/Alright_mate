package main

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"

	_ "github.com/jackc/pgx/v5/stdlib"
)

func request(t *testing.T, handler http.Handler, method, path, body string, want int) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(method, path, strings.NewReader(body))
	if body != "" {
		req.Header.Set("Content-Type", "application/json")
	}
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	if rec.Code != want {
		t.Fatalf("%s %s returned %d, want %d: %s", method, path, rec.Code, want, rec.Body)
	}
	return rec
}

func TestExpressionValidation(t *testing.T) {
	handler := newHandler(nil, slog.New(slog.NewJSONHandler(io.Discard, nil)))
	request(t, handler, "GET", "/livez", "", 200)
	request(t, handler, "POST", "/api/expressions", "", 415)
	for _, invalid := range []string{
		`{"phrase":" ","meaning":"valid"}`,
		`{"phrase":"valid","meaning":""}`,
		`{"phrase":"valid","meaning":"valid","unexpected":true}`,
		`{"phrase":"valid","meaning":"valid"}{}`,
		`{"phrase":"` + strings.Repeat("x", 121) + `","meaning":"valid"}`,
		`{"phrase":"` + strings.Repeat("x", 5000) + `","meaning":"valid"}`,
	} {
		want := 400
		if len(invalid) > 4096 {
			want = 413
		}
		request(t, handler, "POST", "/api/expressions", invalid, want)
	}
}

func TestExpressionsPersist(t *testing.T) {
	dsn := os.Getenv("TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("set TEST_DATABASE_URL to a disposable PostgreSQL database with schema.sql applied")
	}
	open := func() *sql.DB {
		t.Helper()
		db, err := sql.Open("pgx", dsn)
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { db.Close() })
		return db
	}
	logger := slog.New(slog.NewJSONHandler(io.Discard, nil))
	db := open()
	handler := newHandler(db, logger)
	request(t, handler, "GET", "/readyz", "", 200)
	phrase := fmt.Sprintf("Test expression %d", time.Now().UnixNano())
	body, err := json.Marshal(map[string]string{"phrase": phrase, "meaning": "A persistence check"})
	if err != nil {
		t.Fatal(err)
	}
	created := request(t, handler, "POST", "/api/expressions", string(body), 201)
	var saved expression
	if err := json.Unmarshal(created.Body.Bytes(), &saved); err != nil || saved.ID == 0 || saved.Phrase != phrase || saved.Meaning != "A persistence check" {
		t.Fatalf("unexpected created expression %s", created.Body)
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	request(t, handler, "GET", "/readyz", "", 503)
	request(t, handler, "GET", "/livez", "", 200)
	reopened := open()
	t.Cleanup(func() {
		if _, err := reopened.Exec("DELETE FROM expressions WHERE id = $1", saved.ID); err != nil {
			t.Errorf("cleanup test row: %v", err)
		}
	})
	afterRestart := request(t, newHandler(reopened, logger), "GET", "/api/expressions", "", 200)
	var list []expression
	if err := json.Unmarshal(afterRestart.Body.Bytes(), &list); err != nil {
		t.Fatal(err)
	}
	for _, item := range list {
		if item == saved {
			return
		}
	}
	t.Fatal("new connection did not see the inserted expression")
}

func TestLoopbackHosts(t *testing.T) {
	handler := restrictLoopbackHosts(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	}))
	for _, tc := range []struct {
		host string
		want int
	}{
		{"127.0.0.1:8080", 204},
		{"localhost", 204},
		{"[::1]:8080", 204},
		{"external.example:8080", 400},
	} {
		req := httptest.NewRequest("GET", "/", nil)
		req.Host = tc.host
		rec := httptest.NewRecorder()
		handler.ServeHTTP(rec, req)
		if rec.Code != tc.want {
			t.Errorf("host %q returned %d, want %d", tc.host, rec.Code, tc.want)
		}
	}
}
