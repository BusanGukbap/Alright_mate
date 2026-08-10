package main

import (
	"context"
	"io"
	"net"
	"net/http"
	"strings"
	"testing"
	"time"
)

func TestLoadConfigRequiresPrivateRuntimeConfiguration(t *testing.T) {
	tests := []struct {
		name    string
		values  map[string]string
		missing string
	}{
		{"database URL", map[string]string{"OWNER_GITHUB_LOGIN": "octocat", "TRUSTED_OWNER_HEADER": "X-Owner"}, "DATABASE_URL"},
		{"owner login", map[string]string{"DATABASE_URL": "postgres://example", "TRUSTED_OWNER_HEADER": "X-Owner"}, "OWNER_GITHUB_LOGIN"},
		{"trusted header", map[string]string{"DATABASE_URL": "postgres://example", "OWNER_GITHUB_LOGIN": "octocat"}, "TRUSTED_OWNER_HEADER"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			_, err := loadConfig(mapLookup(test.values))
			if err == nil || !strings.Contains(err.Error(), test.missing) {
				t.Fatalf("loadConfig error = %v, want missing %s", err, test.missing)
			}
		})
	}
}

func TestLoadConfigAppliesSafeDefaultsAndEnvironmentOverrides(t *testing.T) {
	configuration, err := loadConfig(mapLookup(map[string]string{
		"DATABASE_URL":         "postgres://example",
		"OWNER_GITHUB_LOGIN":   "octocat",
		"TRUSTED_OWNER_HEADER": "X-Alright-Mate-Owner",
		"API_LISTEN_ADDRESS":   "127.0.0.1:9090",
		"BUILD_VERSION":        "abc123",
	}))
	if err != nil {
		t.Fatalf("loadConfig: %v", err)
	}
	if configuration.ListenAddress != "127.0.0.1:9090" || configuration.BuildVersion != "abc123" {
		t.Fatalf("environment overrides = %#v", configuration)
	}
	for name, value := range map[string]time.Duration{
		"read header": configuration.ReadHeaderTimeout,
		"read":        configuration.ReadTimeout,
		"write":       configuration.WriteTimeout,
		"idle":        configuration.IdleTimeout,
		"readiness":   configuration.ReadinessTimeout,
		"shutdown":    configuration.ShutdownTimeout,
	} {
		if value <= 0 {
			t.Errorf("%s timeout = %s, want positive", name, value)
		}
	}
}

func TestNewHTTPServerUsesConfiguredTimeouts(t *testing.T) {
	handler := &noopHandler{}
	configuration := config{
		ListenAddress:     "127.0.0.1:9090",
		ReadHeaderTimeout: 3 * time.Second,
		ReadTimeout:       4 * time.Second,
		WriteTimeout:      5 * time.Second,
		IdleTimeout:       6 * time.Second,
	}
	server := newHTTPServer(configuration, handler)
	if server.Addr != configuration.ListenAddress || server.Handler != handler || server.ReadHeaderTimeout != 3*time.Second || server.ReadTimeout != 4*time.Second || server.WriteTimeout != 5*time.Second || server.IdleTimeout != 6*time.Second {
		t.Fatalf("HTTP server = %#v", server)
	}
}

func TestRunServerGracefullyDrainsInflightRequest(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	started := make(chan struct{})
	release := make(chan struct{})
	server := &http.Server{Handler: http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		close(started)
		<-release
		writer.WriteHeader(http.StatusNoContent)
	})}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() {
		done <- runServer(ctx, server, listener, time.Second)
	}()

	responseDone := make(chan error, 1)
	go func() {
		response, requestErr := http.Get("http://" + listener.Addr().String())
		if requestErr != nil {
			responseDone <- requestErr
			return
		}
		defer response.Body.Close()
		_, _ = io.Copy(io.Discard, response.Body)
		if response.StatusCode != http.StatusNoContent {
			responseDone <- &statusError{got: response.StatusCode, want: http.StatusNoContent}
			return
		}
		responseDone <- nil
	}()
	select {
	case <-started:
	case <-time.After(time.Second):
		t.Fatal("request did not start")
	}
	cancel()
	close(release)

	if err := <-responseDone; err != nil {
		t.Fatalf("in-flight response: %v", err)
	}
	if err := <-done; err != nil {
		t.Fatalf("runServer: %v", err)
	}
}

func mapLookup(values map[string]string) func(string) (string, bool) {
	return func(name string) (string, bool) {
		value, ok := values[name]
		return value, ok
	}
}

type statusError struct {
	got  int
	want int
}

type noopHandler struct{}

func (*noopHandler) ServeHTTP(http.ResponseWriter, *http.Request) {}

func (err *statusError) Error() string {
	return http.StatusText(err.got) + ", want " + http.StatusText(err.want)
}
