FROM golang:1.22-alpine AS build
WORKDIR /src

# Download deps first (cache layer)
COPY go.mod go.sum ./
RUN go mod download

# Copy generated proto stubs and gateway source
COPY gen/       gen/
COPY gateway/   gateway/

RUN CGO_ENABLED=0 go build -o /gateway ./gateway/cmd/gateway

# ── Runtime image ─────────────────────────────────────────────────
FROM scratch
COPY --from=build /gateway /gateway
EXPOSE 50051
ENTRYPOINT ["/gateway"]
