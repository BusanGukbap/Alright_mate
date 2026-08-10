# Owner Administration Specification

## Trusted identity

Owner routes are protected at the gateway. oauth2-proxy authenticates the configured GitHub login; the gateway removes any client-provided identity headers and passes one trusted owner header to the API. That header is an internal gateway-to-API assertion, never a public API credential and never a caller-supplied OpenAPI parameter. The API accepts an owner route only when that trusted identity exactly matches configured owner identity. The API neither creates users nor maintains sessions.

Unauthenticated or untrusted owner requests return 401 or 403 RFC 9457 problem documents as appropriate. Public routes do not rely on this header.

## Create and amend

`POST /api/v1/admin/expressions` creates an expression as `draft` unless `status: published` is explicitly requested and all publication rules pass. Creation accepts only `draft` or `published`; it never accepts `archived`. Required content is a non-empty British English `expression` and a non-empty Korean `meaningKo`. `note`, `sourceUrl` and tags are optional for a draft, but any supplied `sourceUrl` must already pass the application validation contract below.

`PATCH /api/v1/admin/expressions/{id}` amends the supplied fields. It supports expression, Korean meaning, public note, tags, source URL and status. A request with no recognised editable field returns 400. It does not create a second expression; duplicate canonical expression text returns 409. The request schema permits only a transition request to `published` or `archived`, never `draft`; the server evaluates the current record and returns 422 for any invalid lifecycle transition or content amendment to an archived record.

## Status lifecycle

`draft` may become `published`; `published` may become `archived`. No other transition is valid: `published → draft`, `archived → draft`, `archived → published`, creation as `archived`, and status mutation of an archived record all return 422. An archived record remains retained but cannot return to publication in the MVP. Updates to content are allowed for drafts and published records; archived records may not be amended. There is no DELETE endpoint.

Every non-null `sourceUrl` write is parsed by the Go API with the standard `net/url` package. The URL must parse without malformed syntax or escapes, be absolute, use only `http` or `https`, have a non-empty hostname, and contain no embedded credentials. Invalid supplied URLs return 422 even for drafts. Direct publication and transition from draft to published additionally require the URL and revalidate any value already saved on the draft; missing or invalid URLs return 422. Publishing records `publishedAt` in UTC. Archiving records `archivedAt` in UTC and removes the expression from public reads and future due queues, while preserving review events.

## Response shape

Admin representations include the expression lifecycle timestamps and may include `reviewState` only on owner routes. Public representations use the separately defined `PublicExpression` schema and cannot gain those fields by extension.
