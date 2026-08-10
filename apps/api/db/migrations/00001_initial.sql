-- +goose Up
CREATE TABLE expressions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    expression text NOT NULL,
    meaning_ko text NOT NULL,
    note text,
    source_url text,
    status text NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    published_at timestamptz,
    archived_at timestamptz,
    CONSTRAINT expressions_expression_trimmed_nonempty_check
        CHECK (expression = btrim(expression) AND expression <> ''),
    CONSTRAINT expressions_meaning_ko_trimmed_nonempty_check
        CHECK (meaning_ko = btrim(meaning_ko) AND meaning_ko <> ''),
    CONSTRAINT expressions_status_check
        CHECK (status IN ('draft', 'published', 'archived')),
    CONSTRAINT expressions_lifecycle_timestamps_check CHECK (
        (status = 'draft' AND published_at IS NULL AND archived_at IS NULL)
        OR (status = 'published' AND published_at IS NOT NULL AND archived_at IS NULL)
        OR (status = 'archived' AND published_at IS NOT NULL AND archived_at IS NOT NULL)
    ),
    CONSTRAINT expressions_source_url_format_check CHECK (
        source_url IS NULL
        OR (
            btrim(source_url) <> ''
            AND source_url ~* '^https?://[^/?#[:space:]]+(?:[/?#][^[:space:]]*)?$'
        )
    ),
    CONSTRAINT expressions_published_source_url_check CHECK (
        status = 'draft' OR source_url IS NOT NULL
    )
);

CREATE UNIQUE INDEX expressions_lower_expression_key
    ON expressions (lower(expression));
CREATE INDEX expressions_public_browse_idx
    ON expressions (status, published_at DESC, id DESC);
CREATE INDEX expressions_search_idx
    ON expressions USING GIN (to_tsvector('simple', expression || ' ' || meaning_ko || ' ' || coalesce(note, '')));

CREATE TABLE expression_tags (
    expression_id uuid NOT NULL,
    tag text NOT NULL,
    PRIMARY KEY (expression_id, tag),
    CONSTRAINT expression_tags_expression_id_fkey
        FOREIGN KEY (expression_id) REFERENCES expressions(id) ON DELETE RESTRICT,
    CONSTRAINT expression_tags_tag_check CHECK (
        tag = btrim(tag)
        AND tag = lower(tag)
        AND tag ~ '^[a-z0-9]([a-z0-9-]{0,38}[a-z0-9])?$'
    )
);

CREATE INDEX expression_tags_tag_expression_id_idx
    ON expression_tags (tag, expression_id);

CREATE TABLE expression_review_state (
    expression_id uuid PRIMARY KEY,
    interval_days smallint NOT NULL,
    due_at timestamptz NOT NULL,
    last_reviewed_at timestamptz,
    updated_at timestamptz NOT NULL,
    CONSTRAINT expression_review_state_expression_id_fkey
        FOREIGN KEY (expression_id) REFERENCES expressions(id) ON DELETE RESTRICT,
    CONSTRAINT expression_review_state_interval_days_check
        CHECK (interval_days IN (1, 3, 7, 14, 30))
);

CREATE INDEX expression_review_state_due_at_expression_id_idx
    ON expression_review_state (due_at, expression_id);

CREATE TABLE review_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    expression_id uuid NOT NULL,
    rating text NOT NULL,
    reviewed_at timestamptz NOT NULL,
    review_date_seoul date GENERATED ALWAYS AS ((reviewed_at AT TIME ZONE 'Asia/Seoul')::date) STORED NOT NULL,
    previous_interval_days smallint NOT NULL,
    next_interval_days smallint NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT review_events_expression_id_fkey
        FOREIGN KEY (expression_id) REFERENCES expressions(id) ON DELETE RESTRICT,
    CONSTRAINT review_events_rating_check CHECK (rating IN ('again', 'good')),
    CONSTRAINT review_events_previous_interval_days_check
        CHECK (previous_interval_days IN (1, 3, 7, 14, 30)),
    CONSTRAINT review_events_next_interval_days_check
        CHECK (next_interval_days IN (1, 3, 7, 14, 30)),
    CONSTRAINT review_events_expression_review_date_seoul_key
        UNIQUE (expression_id, review_date_seoul)
);

CREATE INDEX review_events_review_date_seoul_expression_id_idx
    ON review_events (review_date_seoul, expression_id);
CREATE INDEX review_events_expression_id_reviewed_at_idx
    ON review_events (expression_id, reviewed_at DESC);

-- +goose Down
DROP TABLE review_events;
DROP TABLE expression_review_state;
DROP TABLE expression_tags;
DROP TABLE expressions;
