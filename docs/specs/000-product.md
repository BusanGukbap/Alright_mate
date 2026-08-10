# Alright, Mate? Product Specification

## Purpose and audience

Alright, Mate? is a personal British English expression journal for one trusted owner and public readers. It helps the owner collect useful expressions with Korean explanations, publish the reviewed entries, and practise them with a small spaced-review queue. Readers can browse only the expressions the owner has published.

The product is deliberately not a language course, social network or AI tutor. Its primary audience is a Korean-speaking learner who wants a calm, curated record of British English, with a simple public reference for visitors.

## Core journeys

1. A reader opens the public list, searches its expression text or Korean meaning, filters by a tag, opens a detail page, and follows the cited source if desired.
2. The trusted owner signs in through the gateway, saves an expression as a draft, then publishes it only after providing a valid source URL.
3. The owner opens the due queue, rates an expression `again` or `good`, and sees the queue and thirty-day progress update.
4. An operator checks liveness and readiness before directing traffic, observes metrics, and follows the documented backup, restore, deployment and rollback procedures.

## Success criteria

- A public request returns only `published` expressions and never exposes review state.
- The owner can create, amend, publish and archive expressions without physically deleting history.
- Review scheduling uses the exact intervals 1, 3, 7, 14 and 30 days; all instant timestamps are UTC and review dates are Asia/Seoul dates.
- A duplicate review for the same expression on the same Korean date is rejected with HTTP 409.
- Every approved HTTP interface is defined in `specs/openapi.yaml` and errors use RFC 9457 problem documents.

## Non-goals

- Multi-user accounts, roles or sharing controls.
- Audio, pronunciation recording or media upload.
- Runtime AI, generated explanations or chat.
- Kubernetes, Helm, Argo CD or other cluster deployment in the MVP.
- Physical deletion of expressions or review history.

## Editorial completion boundary

The original portfolio completion gate requires at least twelve owner-reviewed published expressions. The repository contains twelve sourced candidates, but every editorial checkbox remains deliberately unticked. AI, tests and seed data cannot satisfy this gate on the owner's behalf; product acceptance remains pending until the owner understands and approves each item before entering or publishing it.

## Prompt Contract

| Item | Contract |
| --- | --- |
| Goal | Preserve and practise curated British English expressions with Korean explanations. |
| Inputs | Owner-authenticated expression data, rating events and public browse queries. |
| Required outcomes | Correct visibility, source-gated publishing, deterministic review scheduling, traceable operations. |
| Constraints | British English prose; one trusted owner; PostgreSQL system of record; UTC instants and Asia/Seoul review dates; RFC 9457 errors. |
| Failure conditions | Any public review data, un-sourced publication, non-deterministic interval change, duplicate same-day review acceptance, or scope expansion into a non-goal. |
| Output format | Versioned feature specifications, OpenAPI 3.1 contract, data model and ADRs before production code. |
