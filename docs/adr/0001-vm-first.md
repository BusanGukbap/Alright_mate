# ADR 0001: Use a Seoul VM for the MVP

## Status

Accepted.

## Context

Alright, Mate? needs one small, operable production environment and has one trusted owner. The MVP needs a private API/database network, a gateway and dependable backup rather than cluster scheduling.

## Decision

Run the MVP on a Seoul-region VM with Docker Compose. Nginx is the only host-port gateway. API and PostgreSQL remain private to the Compose network.

## Consequences

The deployment is inexpensive and understandable, while high availability and automatic cluster recovery are intentionally deferred. Operations must document backup, restoration and rollback.
