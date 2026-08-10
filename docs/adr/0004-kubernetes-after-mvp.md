# ADR 0004: Defer Kubernetes until after the MVP

## Status

Accepted.

## Context

Kubernetes would add cluster operations, deployment objects and security surfaces that are disproportionate to a single-owner learning journal.

## Decision

Do not use Kubernetes, Helm or Argo CD for the MVP. Reassess only after concrete scale, availability or multi-service requirements exist.

## Consequences

The repository remains focused on VM and Compose operation. Future Kubernetes adoption requires a separate ADR and migration plan rather than an implied runtime dependency.
