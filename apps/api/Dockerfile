# syntax=docker/dockerfile:1.12@sha256:93bfd3b68c109427185cd78b4779fc82b484b0b7618e36d0f104d4d801e66d25

ARG BUILD_VERSION=development
ARG VCS_REF=unknown

FROM golang:1.26.5-alpine3.24@sha256:0178a641fbb4858c5f1b48e34bdaabe0350a330a1b1149aabd498d0699ff5fb2 AS dependencies
WORKDIR /src

COPY go.mod go.sum ./
RUN --mount=type=cache,target=/go/pkg/mod \
    go mod download

FROM dependencies AS api-build
COPY . ./
RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    CGO_ENABLED=0 GOOS=linux go build \
      -mod=readonly \
      -trimpath -buildvcs=false -ldflags="-s -w" \
      -o /out/api ./cmd/api

FROM dependencies AS goose-dependencies
WORKDIR /tools/goose
COPY tools/goose/go.mod tools/goose/go.sum ./
RUN --mount=type=cache,target=/go/pkg/mod \
    go mod download

FROM goose-dependencies AS migration-build
RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    CGO_ENABLED=0 GOOS=linux go build \
      -mod=readonly \
      -tags="no_clickhouse no_libsql no_mssql no_mysql no_redshift no_spanner no_sqlite3 no_starrocks no_tidb no_turso no_vertica no_ydb" \
      -trimpath -buildvcs=false -ldflags="-s -w" \
      -o /out/goose github.com/pressly/goose/v3/cmd/goose

FROM busybox:1.37.0-musl@sha256:fc6dddc4c44b1bfe37f41cae8e67d1693828e8f42a91862816d7953e2c9d3f23 AS migration
ARG BUILD_VERSION
ARG VCS_REF
LABEL org.opencontainers.image.title="Alright, Mate? database migration" \
      org.opencontainers.image.version="${BUILD_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.source="https://github.com/example/alright-mate"

COPY --from=migration-build --chown=65532:65532 /out/goose /usr/local/bin/goose
COPY --chown=65532:65532 db/migrations /migrations

USER 65532:65532
ENV GOOSE_DRIVER=postgres \
    GOOSE_MIGRATION_DIR=/migrations \
    NO_COLOR=1
ENTRYPOINT ["/usr/local/bin/goose"]
CMD ["up"]

FROM busybox:1.37.0-musl@sha256:fc6dddc4c44b1bfe37f41cae8e67d1693828e8f42a91862816d7953e2c9d3f23 AS api
ARG BUILD_VERSION
ARG VCS_REF
LABEL org.opencontainers.image.title="Alright, Mate? API" \
      org.opencontainers.image.version="${BUILD_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.source="https://github.com/example/alright-mate"

WORKDIR /app
COPY --from=api-build --chown=65532:65532 /out/api /app/api

USER 65532:65532
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/healthz"]
ENTRYPOINT ["/app/api"]
