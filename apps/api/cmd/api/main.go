package main

import (
	"context"
	"errors"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"alrightmate/api/internal/application"
	"alrightmate/api/internal/httpapi"
	"github.com/jackc/pgx/v5/pgxpool"
)

type config struct {
	ListenAddress      string
	DatabaseURL        string
	OwnerLogin         string
	TrustedOwnerHeader string
	BuildVersion       string
	ReadHeaderTimeout  time.Duration
	ReadTimeout        time.Duration
	WriteTimeout       time.Duration
	IdleTimeout        time.Duration
	ReadinessTimeout   time.Duration
	ShutdownTimeout    time.Duration
}

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if err := run(ctx, os.LookupEnv); err != nil {
		log.Printf("API stopped: %v", err)
		os.Exit(1)
	}
}

func run(ctx context.Context, lookup func(string) (string, bool)) error {
	configuration, err := loadConfig(lookup)
	if err != nil {
		return err
	}
	poolConfiguration, err := pgxpool.ParseConfig(configuration.DatabaseURL)
	if err != nil {
		return fmt.Errorf("parse DATABASE_URL: %w", err)
	}
	pool, err := pgxpool.NewWithConfig(ctx, poolConfiguration)
	if err != nil {
		return fmt.Errorf("open PostgreSQL pool: %w", err)
	}
	defer pool.Close()

	pingContext, cancelPing := context.WithTimeout(ctx, configuration.ReadinessTimeout)
	err = pool.Ping(pingContext)
	cancelPing()
	if err != nil {
		return fmt.Errorf("connect to PostgreSQL: %w", err)
	}

	service := application.NewService(pool, nil)
	handler, err := httpapi.NewHandler(service, pool, httpapi.Config{
		OwnerLogin:         configuration.OwnerLogin,
		TrustedOwnerHeader: configuration.TrustedOwnerHeader,
		ReadinessTimeout:   configuration.ReadinessTimeout,
		BuildVersion:       configuration.BuildVersion,
	})
	if err != nil {
		return fmt.Errorf("construct HTTP handler: %w", err)
	}
	listener, err := net.Listen("tcp", configuration.ListenAddress)
	if err != nil {
		return fmt.Errorf("listen on %s: %w", configuration.ListenAddress, err)
	}
	server := newHTTPServer(configuration, handler)
	return runServer(ctx, server, listener, configuration.ShutdownTimeout)
}

func loadConfig(lookup func(string) (string, bool)) (config, error) {
	required := func(name string) (string, error) {
		value, exists := lookup(name)
		if !exists || strings.TrimSpace(value) == "" {
			return "", fmt.Errorf("%s is required", name)
		}
		return value, nil
	}

	databaseURL, err := required("DATABASE_URL")
	if err != nil {
		return config{}, err
	}
	ownerLogin, err := required("OWNER_GITHUB_LOGIN")
	if err != nil {
		return config{}, err
	}
	trustedOwnerHeader, err := required("TRUSTED_OWNER_HEADER")
	if err != nil {
		return config{}, err
	}
	listenAddress := ":8080"
	if value, exists := lookup("API_LISTEN_ADDRESS"); exists && strings.TrimSpace(value) != "" {
		listenAddress = value
	}
	buildVersion := "development"
	if value, exists := lookup("BUILD_VERSION"); exists && strings.TrimSpace(value) != "" {
		buildVersion = value
	}

	return config{
		ListenAddress:      listenAddress,
		DatabaseURL:        databaseURL,
		OwnerLogin:         ownerLogin,
		TrustedOwnerHeader: trustedOwnerHeader,
		BuildVersion:       buildVersion,
		ReadHeaderTimeout:  5 * time.Second,
		ReadTimeout:        15 * time.Second,
		WriteTimeout:       30 * time.Second,
		IdleTimeout:        60 * time.Second,
		ReadinessTimeout:   2 * time.Second,
		ShutdownTimeout:    10 * time.Second,
	}, nil
}

func newHTTPServer(configuration config, handler http.Handler) *http.Server {
	return &http.Server{
		Addr:              configuration.ListenAddress,
		Handler:           handler,
		ReadHeaderTimeout: configuration.ReadHeaderTimeout,
		ReadTimeout:       configuration.ReadTimeout,
		WriteTimeout:      configuration.WriteTimeout,
		IdleTimeout:       configuration.IdleTimeout,
		MaxHeaderBytes:    1 << 20,
	}
}

func runServer(ctx context.Context, server *http.Server, listener net.Listener, shutdownTimeout time.Duration) error {
	serveErrors := make(chan error, 1)
	go func() {
		serveErrors <- server.Serve(listener)
	}()

	select {
	case err := <-serveErrors:
		if errors.Is(err, http.ErrServerClosed) {
			return nil
		}
		return fmt.Errorf("serve HTTP: %w", err)
	case <-ctx.Done():
		shutdownContext, cancel := context.WithTimeout(context.Background(), shutdownTimeout)
		defer cancel()
		if err := server.Shutdown(shutdownContext); err != nil {
			return fmt.Errorf("gracefully shut down HTTP server: %w", err)
		}
		if err := <-serveErrors; err != nil && !errors.Is(err, http.ErrServerClosed) {
			return fmt.Errorf("serve HTTP during shutdown: %w", err)
		}
		return nil
	}
}
