# Review Specification

## Due queue

`GET /api/v1/admin/reviews/due` returns authenticated-owner expressions that are published, have review state, and have `dueAt <= now`. It is ordered by earliest due time, then expression ID. Archived and draft expressions are excluded. `limit` defaults to 20 and has a maximum of 100.

An expression receives its initial review state at publication: interval 1 day and a due time calculated from the publication instant. The due queue itself does not mutate state.

## Rating state machine

Allowed ratings are `again` and `good`. The current interval is always one of 1, 3, 7, 14 or 30 days.

| Current interval | `again` next interval | `good` next interval |
| --- | ---: | ---: |
| 1 | 1 | 3 |
| 3 | 1 | 7 |
| 7 | 1 | 14 |
| 14 | 1 | 30 |
| 30 | 1 | 30 |

`POST /api/v1/admin/review-events` records the rating, updates the expression review state atomically, and calculates `dueAt` by adding the next interval to the UTC review instant. The database derives `reviewDateSeoul` from that instant in `Asia/Seoul`; clients cannot supply it.

## Duplicate rule

At most one review event may exist for an expression for one `reviewDateSeoul`. A second event, regardless of rating, returns HTTP 409 with the stable RFC URI problem type `urn:alright-mate:problem:review-already-recorded`. The existing state and event remain unchanged.

## Progress

`GET /api/v1/admin/progress?days=30` returns exactly `days` Asia/Seoul local dates in the requested inclusive period, including dates with zero reviews, ordered ascending by date. `days` defaults to 30, and must be 1 through 90. `startDate` is the earliest returned date, `endDate` is the latest, and `entries` contains every calendar date between them with no duplicates or gaps. For each entry, `total = again + good`; the response `total` is the sum of all entry totals. Progress is owner-only and is based on review event dates, not due dates.
