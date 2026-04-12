.PHONY: proto proto-go proto-python proto-clean up down fmt lint

PROTO_SRC  := proto/events.proto
GEN_GO     := gen/go/telescope/v1
GEN_PYTHON := gen/python
PYTHON     := .venv/bin/python

# ── Proto codegen ──────────────────────────────────────────────────
# Requires: protoc, protoc-gen-go, protoc-gen-go-grpc, grpc_tools_node_protoc_plugin (python)
# Install: brew install protobuf && go install google.golang.org/protobuf/cmd/protoc-gen-go@latest
#                                && go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@latest
#          pip install grpcio-tools

proto: proto-go proto-python

proto-go:
	mkdir -p $(GEN_GO)
	protoc \
		--proto_path=proto \
		--go_out=$(GEN_GO) --go_opt=paths=source_relative \
		--go-grpc_out=$(GEN_GO) --go-grpc_opt=paths=source_relative \
		$(PROTO_SRC)

proto-python:
	mkdir -p $(GEN_PYTHON)
	$(PYTHON) -m grpc_tools.protoc \
		--proto_path=proto \
		--python_out=$(GEN_PYTHON) \
		--grpc_python_out=$(GEN_PYTHON) \
		$(PROTO_SRC)

proto-clean:
	rm -rf $(GEN_GO) $(GEN_PYTHON)

# ── Infrastructure ─────────────────────────────────────────────────
up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f

# ── Dev ────────────────────────────────────────────────────────────
fmt:
	cd gateway  && go fmt ./...
	cd actions  && go fmt ./...

lint:
	cd gateway  && go vet ./...
	cd actions  && go vet ./...
