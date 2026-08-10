-- name: InsertExpression :one
INSERT INTO expressions (
    expression,
    meaning_ko,
    note,
    source_url,
    status,
    created_at,
    updated_at,
    published_at,
    archived_at
) VALUES (
    sqlc.arg(expression),
    sqlc.arg(meaning_ko),
    sqlc.narg(note),
    sqlc.narg(source_url),
    sqlc.arg(status),
    sqlc.arg(created_at),
    sqlc.arg(updated_at),
    sqlc.narg(published_at),
    sqlc.narg(archived_at)
)
RETURNING id, expression, meaning_ko, note, source_url, status,
          created_at, updated_at, published_at, archived_at;

-- name: GetExpression :one
SELECT e.id,
       e.expression,
       e.meaning_ko,
       e.note,
       e.source_url,
       e.status,
       e.created_at,
       e.updated_at,
       e.published_at,
       e.archived_at,
       ARRAY(
           SELECT et.tag
           FROM expression_tags et
           WHERE et.expression_id = e.id
           ORDER BY et.tag
       )::text[] AS tags
FROM expressions e
WHERE e.id = sqlc.arg(id);

-- name: GetExpressionForUpdate :one
SELECT e.id,
       e.expression,
       e.meaning_ko,
       e.note,
       e.source_url,
       e.status,
       e.created_at,
       e.updated_at,
       e.published_at,
       e.archived_at,
       ARRAY(
           SELECT et.tag
           FROM expression_tags et
           WHERE et.expression_id = e.id
           ORDER BY et.tag
       )::text[] AS tags
FROM expressions e
WHERE e.id = sqlc.arg(id)
FOR UPDATE OF e;

-- name: UpdateExpression :one
UPDATE expressions
SET expression = sqlc.arg(expression),
    meaning_ko = sqlc.arg(meaning_ko),
    note = sqlc.narg(note),
    source_url = sqlc.narg(source_url),
    status = sqlc.arg(status),
    updated_at = sqlc.arg(updated_at),
    published_at = sqlc.narg(published_at),
    archived_at = sqlc.narg(archived_at)
WHERE id = sqlc.arg(id)
RETURNING id, expression, meaning_ko, note, source_url, status,
          created_at, updated_at, published_at, archived_at;

-- name: DeleteExpressionTags :exec
DELETE FROM expression_tags
WHERE expression_id = sqlc.arg(expression_id);

-- name: InsertExpressionTag :exec
INSERT INTO expression_tags (expression_id, tag)
VALUES (sqlc.arg(expression_id), sqlc.arg(tag));

-- name: InsertInitialReviewState :one
INSERT INTO expression_review_state (
    expression_id,
    interval_days,
    due_at,
    last_reviewed_at,
    updated_at
) VALUES (
    sqlc.arg(expression_id),
    1,
    sqlc.arg(due_at),
    NULL,
    sqlc.arg(updated_at)
)
RETURNING expression_id, interval_days, due_at, last_reviewed_at, updated_at;

-- name: GetReviewState :one
SELECT expression_id, interval_days, due_at, last_reviewed_at, updated_at
FROM expression_review_state
WHERE expression_id = sqlc.arg(expression_id);

-- name: CountPublicExpressions :one
SELECT count(*)
FROM expressions e
WHERE e.status = 'published'
  AND (
      sqlc.narg('query')::text IS NULL
      OR strpos(lower(e.expression), lower(sqlc.narg('query')::text)) > 0
      OR strpos(lower(e.meaning_ko), lower(sqlc.narg('query')::text)) > 0
  )
  AND (
      sqlc.narg('tag')::text IS NULL
      OR EXISTS (
          SELECT 1
          FROM expression_tags et
          WHERE et.expression_id = e.id
            AND et.tag = sqlc.narg('tag')::text
      )
  );

-- name: ListPublicExpressions :many
SELECT e.id,
       e.expression,
       e.meaning_ko,
       COALESCE(e.source_url, '')::text AS source_url,
       COALESCE(e.published_at, e.created_at) AS published_at,
       ARRAY(
           SELECT et.tag
           FROM expression_tags et
           WHERE et.expression_id = e.id
           ORDER BY et.tag
       )::text[] AS tags
FROM expressions e
WHERE e.status = 'published'
  AND (
      sqlc.narg('query')::text IS NULL
      OR strpos(lower(e.expression), lower(sqlc.narg('query')::text)) > 0
      OR strpos(lower(e.meaning_ko), lower(sqlc.narg('query')::text)) > 0
  )
  AND (
      sqlc.narg('tag')::text IS NULL
      OR EXISTS (
          SELECT 1
          FROM expression_tags et
          WHERE et.expression_id = e.id
            AND et.tag = sqlc.narg('tag')::text
      )
  )
ORDER BY e.published_at DESC, e.id DESC
LIMIT sqlc.arg(row_limit)::integer
OFFSET sqlc.arg(row_offset)::bigint;

-- name: GetPublicExpression :one
SELECT e.id,
       e.expression,
       e.meaning_ko,
       e.note,
       COALESCE(e.source_url, '')::text AS source_url,
       COALESCE(e.published_at, e.created_at) AS published_at,
       ARRAY(
           SELECT et.tag
           FROM expression_tags et
           WHERE et.expression_id = e.id
           ORDER BY et.tag
       )::text[] AS tags
FROM expressions e
WHERE e.id = sqlc.arg(id)
  AND e.status = 'published';

-- name: ListDueReviews :many
SELECT e.id,
       e.expression,
       e.meaning_ko,
       e.note,
       COALESCE(e.source_url, '')::text AS source_url,
       COALESCE(e.published_at, e.created_at) AS published_at,
       ARRAY(
           SELECT et.tag
           FROM expression_tags et
           WHERE et.expression_id = e.id
           ORDER BY et.tag
       )::text[] AS tags,
       s.interval_days,
       s.due_at,
       s.last_reviewed_at,
       s.updated_at AS review_updated_at
FROM expression_review_state s
JOIN expressions e ON e.id = s.expression_id
WHERE e.status = 'published'
  AND s.due_at <= sqlc.arg(now)
ORDER BY s.due_at, s.expression_id
LIMIT sqlc.arg(row_limit)::integer;

-- name: GetPublishedReviewStateForUpdate :one
SELECT s.expression_id,
       s.interval_days,
       s.due_at,
       s.last_reviewed_at,
       s.updated_at
FROM expression_review_state s
JOIN expressions e ON e.id = s.expression_id
WHERE s.expression_id = sqlc.arg(expression_id)
  AND e.status = 'published'
FOR UPDATE OF e, s;

-- name: InsertReviewEvent :one
INSERT INTO review_events (
    expression_id,
    rating,
    reviewed_at,
    previous_interval_days,
    next_interval_days,
    created_at
) VALUES (
    sqlc.arg(expression_id),
    sqlc.arg(rating),
    sqlc.arg(reviewed_at),
    sqlc.arg(previous_interval_days),
    sqlc.arg(next_interval_days),
    sqlc.arg(created_at)
)
RETURNING id, expression_id, rating, reviewed_at, review_date_seoul,
          previous_interval_days, next_interval_days, created_at;

-- name: UpdateReviewState :one
UPDATE expression_review_state
SET interval_days = sqlc.arg(interval_days),
    due_at = sqlc.arg(due_at),
    last_reviewed_at = sqlc.arg(last_reviewed_at),
    updated_at = sqlc.arg(updated_at)
WHERE expression_id = sqlc.arg(expression_id)
RETURNING expression_id, interval_days, due_at, last_reviewed_at, updated_at;

-- name: ListProgressCounts :many
SELECT review_date_seoul,
       count(*)::integer AS total,
       count(*) FILTER (WHERE rating = 'again')::integer AS again,
       count(*) FILTER (WHERE rating = 'good')::integer AS good
FROM review_events
WHERE review_date_seoul BETWEEN sqlc.arg(start_date)::date AND sqlc.arg(end_date)::date
GROUP BY review_date_seoul
ORDER BY review_date_seoul;
