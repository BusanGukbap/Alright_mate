# Data Model Contract

PostgreSQL is the system of record. All `*_at` columns are `timestamptz`, persisted and exchanged as UTC instants. `review_date_seoul` is a PostgreSQL `date`, derived from a review instant in `Asia/Seoul`; application servers do not derive it from their local timezone.

## `expressions`

| Column | Type | Rules |
| --- | --- | --- |
| `id` | `uuid` | Primary key; generated server-side. |
| `expression` | `text` | Required, trimmed, non-empty; canonical lower-case form is unique. |
| `meaning_ko` | `text` | Required, trimmed, non-empty Korean explanation. |
| `note` | `text` | Nullable public explanatory note. |
| `source_url` | `text` | Nullable in draft; every non-null application write must pass the source URL validation boundary below, and publication requires a non-null value. |
| `status` | enum/text | Required; check: `draft`, `published`, `archived`. |
| `created_at`, `updated_at` | `timestamptz` | Required UTC audit instants. |
| `published_at` | `timestamptz` | Required when status is `published` or `archived`. |
| `archived_at` | `timestamptz` | Required only when status is `archived`. |

Constraints: status/lifecycle timestamp consistency; source URL is non-null for published or archived rows and passes the database defence-in-depth guard below; no physical deletion. Indexes: unique index on `lower(expression)`; public browse index on `(status, published_at DESC, id DESC)`; search index appropriate to the selected PostgreSQL text-search implementation.

### Source URL validation boundary

The application is the normative full-validation boundary for every non-null `source_url` write, including a URL supplied while an expression is still a draft. The Go API must parse the value with the standard library `net/url` package and reject it unless all of the following hold:

- Parsing succeeds, including URI syntax and percent-escape validation.
- The URL is absolute, its scheme is `http` or `https`, and `Hostname()` is non-empty.
- `User` is nil, so embedded credentials such as `user:password@host` are rejected.

An invalid supplied URL returns 422 and is never persisted. Creating directly as `published`, or transitioning an existing draft to `published`, also returns 422 when the required URL is missing or invalid; a previously stored draft URL is revalidated at that transition.

PostgreSQL core has no full URL parser, so its check constraint is deliberately a defence-in-depth guard rather than the normative parser. For every non-null value it requires an HTTP(S) scheme, `://`, a non-empty authority and no whitespace (equivalent to the case-insensitive POSIX pattern `^https?://[^/?#[:space:]]+(?:[/?#][^[:space:]]*)?$`). This database guard does not replace application validation and is not relied upon to detect credentials or every malformed URI.

## `expression_tags`

| Column | Type | Rules |
| --- | --- | --- |
| `expression_id` | `uuid` | Foreign key to `expressions(id)`, restrict physical deletion. |
| `tag` | `text` | Required lower-case, trimmed portable slug matching `^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$` (1–40 characters, starting and ending with an alphanumeric character). |

Primary key is `(expression_id, tag)`. Index `(tag, expression_id)` supports public tag filtering.

## `expression_review_state`

| Column | Type | Rules |
| --- | --- | --- |
| `expression_id` | `uuid` | Primary key and foreign key to `expressions(id)`. |
| `interval_days` | `smallint` | Required; check: 1, 3, 7, 14 or 30. |
| `due_at` | `timestamptz` | Required UTC instant. |
| `last_reviewed_at` | `timestamptz` | Nullable UTC instant. |
| `updated_at` | `timestamptz` | Required UTC instant. |

Index `(due_at, expression_id)` supports the due queue. A state row is created at publishing and retained after archiving for history, but archived expressions are excluded from queries.

## `review_events`

| Column | Type | Rules |
| --- | --- | --- |
| `id` | `uuid` | Primary key; generated server-side. |
| `expression_id` | `uuid` | Required foreign key to `expressions(id)`. |
| `rating` | enum/text | Required; check: `again`, `good`. |
| `reviewed_at` | `timestamptz` | Required UTC review instant. |
| `review_date_seoul` | `date` | Required database-derived `Asia/Seoul` date. |
| `previous_interval_days`, `next_interval_days` | `smallint` | Required; both constrained to 1, 3, 7, 14 or 30. |
| `created_at` | `timestamptz` | Required UTC audit instant. |

Constraints: unique `(expression_id, review_date_seoul)` enforces the duplicate rule; review events are append-only. Index `(review_date_seoul, expression_id)` supports progress queries; index `(expression_id, reviewed_at DESC)` supports history and diagnostics.
