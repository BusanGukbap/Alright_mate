# Public Expressions Specification

## Visibility

Public clients are unauthenticated. They can read only expressions whose status is `published`; drafts and archived expressions behave as absent and return HTTP 404 when addressed by ID. A public representation never includes any review-state field, due date, last-review timestamp or review event.

## List and search

`GET /api/v1/expressions` returns a paginated list ordered by newest publication first, then ID descending for stable pagination. `page` defaults to 1 and `pageSize` defaults to 20 (maximum 100). Invalid pagination is a 400 RFC 9457 response.

- `q` is optional. It performs case-insensitive matching against `expression` and `meaningKo`; whitespace-only input is treated as absent.
- `tag` is optional. It matches one normalised tag exactly; it combines with `q` using AND.
- Each list item includes ID, expression, Korean meaning, tags, source URL and publication time. It does not include the owner-only note.
- Empty results are a successful 200 response with `items: []`, not a 404.

## Detail

`GET /api/v1/expressions/{id}` returns one published expression. Its ID must be a UUID. A malformed ID is 400; an unknown, draft or archived ID is 404. The detail adds the optional owner-written `note` only when it has been deliberately marked public; MVP entries use `note` as public explanatory text and do not contain private owner material.

The `sourceUrl` is rendered as an external reference. It is required for publishing and is an absolute HTTP or HTTPS URL.

## Tags

Tags are owner-curated portable slugs. API values are lower-case, trimmed and unique per expression, matching `^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$` (1–40 characters, starting and ending with an alphanumeric character). Public filtering follows this normalised form. Tag discovery is intentionally out of scope; clients derive available filters from list responses.
