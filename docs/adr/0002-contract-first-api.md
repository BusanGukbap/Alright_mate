# ADR 0002: Define the API contract before implementation

## Status

Accepted.

## Context

The React client and Go API must agree on visibility, validation, error shapes and review semantics before either production implementation exists.

## Decision

Maintain `specs/openapi.yaml` as the versioned OpenAPI 3.1 source of truth. Feature and data specifications define business rules that the OpenAPI schemas and tests trace to. RFC 9457 problem documents are the standard error representation.

## Consequences

Implementation changes require contract review first. The contract prevents accidental public exposure of owner review state and enables generated client types later.
