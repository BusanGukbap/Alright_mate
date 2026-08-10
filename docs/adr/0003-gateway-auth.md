# ADR 0003: Authenticate the owner at the gateway

## Status

Accepted.

## Context

The MVP has one configured owner and no user-management requirement. The public API must stay simple while owner routes need a clear, auditable trust boundary.

## Decision

Use oauth2-proxy with the configured GitHub login at the Nginx gateway. Nginx strips inbound identity headers and sets a trusted identity header only after successful authentication. The API compares that header to its configured owner identity. This assertion is an internal gateway-to-API contract, not an API-key, OAuth token or caller-supplied OpenAPI parameter.

## Consequences

The API does not issue tokens, keep sessions or store user accounts. Gateway configuration becomes security-critical and must be tested for header stripping and route isolation.
