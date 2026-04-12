package main

import (
	"context"
	"fmt"
	"log"
	"net"
	"os"
	"os/signal"
	"syscall"
	"time"

	"go.opentelemetry.io/contrib/instrumentation/google.golang.org/grpc/otelgrpc"
	"google.golang.org/grpc"
	"google.golang.org/grpc/reflection"

	"github.com/telescope-obs/telescope-obs/gateway/internal/db"
	gwgrpc "github.com/telescope-obs/telescope-obs/gateway/internal/grpc"
	gwOtel "github.com/telescope-obs/telescope-obs/gateway/internal/otel"
	telescopev1 "github.com/telescope-obs/telescope-obs/gen/go/telescope/v1"
)

func main() {
	if err := run(); err != nil {
		log.Fatalf("gateway: %v", err)
	}
}

func run() error {
	cfg := configFromEnv()

	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer cancel()

	// ── OTel ────────────────────────────────────────────────────────
	tp, err := gwOtel.Init(ctx, cfg.otelEndpoint)
	if err != nil {
		return fmt.Errorf("init otel: %w", err)
	}
	defer func() {
		shutCtx, shutCancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer shutCancel()
		if err := tp.Shutdown(shutCtx); err != nil {
			log.Printf("otel shutdown: %v", err)
		}
	}()

	// ── Database ─────────────────────────────────────────────────────
	store, err := db.New(ctx, cfg.dbURL)
	if err != nil {
		return fmt.Errorf("init db: %w", err)
	}
	defer store.Close()

	// ── gRPC server ───────────────────────────────────────────────────
	// otelgrpc.NewServerHandler() installs W3C traceparent extraction so
	// every RPC automatically becomes a child of the calling service's span.
	grpcSrv := grpc.NewServer(
		grpc.StatsHandler(otelgrpc.NewServerHandler()),
	)

	telescopev1.RegisterTelemetryGatewayServer(grpcSrv, gwgrpc.NewServer(store))

	// gRPC reflection lets grpcurl / Grafana explore the service without
	// having the proto files locally.
	reflection.Register(grpcSrv)

	lis, err := net.Listen("tcp", cfg.listenAddr)
	if err != nil {
		return fmt.Errorf("listen %s: %w", cfg.listenAddr, err)
	}

	log.Printf("gateway listening on %s", cfg.listenAddr)

	errCh := make(chan error, 1)
	go func() {
		errCh <- grpcSrv.Serve(lis)
	}()

	select {
	case <-ctx.Done():
		log.Println("shutting down...")
		grpcSrv.GracefulStop()
		return nil
	case err := <-errCh:
		return fmt.Errorf("grpc serve: %w", err)
	}
}

// ── Config ────────────────────────────────────────────────────────

type config struct {
	listenAddr   string
	dbURL        string
	otelEndpoint string
}

func configFromEnv() config {
	return config{
		listenAddr:   envOr("GATEWAY_ADDR", ":50051"),
		dbURL:        envOr("DB_URL", "postgres://telescope:telescope@db:5432/telescope_obs"),
		otelEndpoint: envOr("OTEL_ENDPOINT", "otel-collector:4317"),
	}
}

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
