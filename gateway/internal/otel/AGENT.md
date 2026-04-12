# gateway/internal/otel/ — Agent Context

`setup.go` initialises the global OTel `TracerProvider` for the Go gateway service.

## What it does

- Creates an OTLP/gRPC exporter connected to the OTel collector endpoint.
- Builds a `Resource` with `service.name = "telescope-obs-gateway"` and host info.
- Registers the provider globally so `otel.Tracer(...)` calls anywhere in the process
  automatically use it.
- Installs W3C `TraceContext` + `Baggage` propagators so the `otelgrpc` middleware
  can extract/inject `traceparent` headers from gRPC metadata.
- Uses `AlwaysSample()` — every span is recorded (appropriate for a low-volume demo).

## Usage

```go
tp, err := gwOtel.Init(ctx, cfg.otelEndpoint)
// ...
defer tp.Shutdown(shutCtx)
```

`Shutdown` flushes any buffered spans before the process exits. It is called in
`main.go` with a 5-second timeout.

## Note on Python vs Go OTel init

The Python pipelines use `lib/python/telescope_obs/otel.py::setup_otel()` which
additionally sets up `MeterProvider` (metrics) and `LoggerProvider` (logs). The Go
gateway only needs traces — metrics and logs are not instrumented on the Go side.
