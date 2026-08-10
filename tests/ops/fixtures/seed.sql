INSERT INTO expressions (
    id,
    expression,
    meaning_ko,
    note,
    source_url,
    status,
    created_at,
    updated_at,
    published_at,
    archived_at
)
VALUES (
    '11111111-1111-4111-8111-111111111111',
    'alright, mate',
    '친근한 인사',
    'synthetic restore-drill row',
    'https://example.invalid/alright-mate',
    'published',
    '2026-08-08T00:00:00Z',
    '2026-08-08T00:00:00Z',
    '2026-08-08T00:00:00Z',
    NULL
);

INSERT INTO expression_tags (expression_id, tag)
VALUES ('11111111-1111-4111-8111-111111111111', 'greeting');

INSERT INTO expression_review_state (
    expression_id,
    interval_days,
    due_at,
    last_reviewed_at,
    updated_at
)
VALUES (
    '11111111-1111-4111-8111-111111111111',
    7,
    '2026-08-15T00:00:00Z',
    '2026-08-08T00:00:00Z',
    '2026-08-08T00:00:00Z'
);

INSERT INTO review_events (
    id,
    expression_id,
    rating,
    reviewed_at,
    previous_interval_days,
    next_interval_days,
    created_at
)
VALUES (
    '22222222-2222-4222-8222-222222222222',
    '11111111-1111-4111-8111-111111111111',
    'good',
    '2026-08-08T00:00:00Z',
    3,
    7,
    '2026-08-08T00:00:00Z'
);
