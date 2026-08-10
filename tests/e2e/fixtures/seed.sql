BEGIN;

TRUNCATE review_events, expression_review_state, expression_tags, expressions;

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
) VALUES
(
    '11111111-1111-4111-8111-111111111111',
    'Fancy a cuppa?',
    '차 한 잔 할래요?',
    'A friendly way to offer someone a cup of tea.',
    'https://example.test/sources/fancy-a-cuppa',
    'published',
    '2026-08-01T09:00:00Z',
    '2026-08-01T09:00:00Z',
    '2026-08-01T09:00:00Z',
    NULL
),
(
    '33333333-3333-4333-8333-333333333333',
    'Private draft phrase',
    '비공개 초안 표현',
    'This draft must never appear publicly.',
    NULL,
    'draft',
    '2026-08-02T09:00:00Z',
    '2026-08-02T09:00:00Z',
    NULL,
    NULL
),
(
    '44444444-4444-4444-8444-444444444444',
    'Archived phrase',
    '보관된 표현',
    'This archived entry remains retained.',
    'https://example.test/sources/archived-phrase',
    'archived',
    '2026-07-01T09:00:00Z',
    '2026-08-02T09:00:00Z',
    '2026-07-01T09:00:00Z',
    '2026-08-02T09:00:00Z'
),
(
    '55555555-5555-4555-8555-555555555555',
    'Review this again',
    '다시 복습할 표현',
    'The first due expression exercises the Again path.',
    'https://example.test/sources/review-again',
    'published',
    '2026-07-02T09:00:00Z',
    '2026-07-02T09:00:00Z',
    '2026-07-02T09:00:00Z',
    NULL
),
(
    '66666666-6666-4666-8666-666666666666',
    'Review this as good',
    '잘 기억한 표현',
    'The second due expression exercises the Good path.',
    'https://example.test/sources/review-good',
    'published',
    '2026-07-03T09:00:00Z',
    '2026-07-03T09:00:00Z',
    '2026-07-03T09:00:00Z',
    NULL
),
(
    '77777777-7777-4777-8777-777777777777',
    'Cuppa on the go',
    '이동 중 마시는 차',
    'Contains the search phrase but not the tea tag.',
    'https://example.test/sources/cuppa-on-the-go',
    'published',
    '2026-08-04T09:00:00Z',
    '2026-08-04T09:00:00Z',
    '2026-08-04T09:00:00Z',
    NULL
),
(
    '88888888-8888-4888-8888-888888888888',
    'Tea before the train',
    '기차 전에 마시는 차',
    'Carries the tea tag but not the search phrase.',
    'https://example.test/sources/tea-before-the-train',
    'published',
    '2026-08-05T09:00:00Z',
    '2026-08-05T09:00:00Z',
    '2026-08-05T09:00:00Z',
    NULL
);

INSERT INTO expression_tags (expression_id, tag)
VALUES
    ('11111111-1111-4111-8111-111111111111', 'casual'),
    ('11111111-1111-4111-8111-111111111111', 'tea'),
    ('33333333-3333-4333-8333-333333333333', 'private'),
    ('44444444-4444-4444-8444-444444444444', 'retired'),
    ('55555555-5555-4555-8555-555555555555', 'review'),
    ('66666666-6666-4666-8666-666666666666', 'review'),
    ('77777777-7777-4777-8777-777777777777', 'casual'),
    ('88888888-8888-4888-8888-888888888888', 'tea');

INSERT INTO expression_review_state (
    expression_id,
    interval_days,
    due_at,
    last_reviewed_at,
    updated_at
) VALUES
(
    '11111111-1111-4111-8111-111111111111',
    30,
    '2099-01-01T00:00:00Z',
    NULL,
    '2026-08-01T09:00:00Z'
),
(
    '44444444-4444-4444-8444-444444444444',
    14,
    '2026-07-15T09:00:00Z',
    NULL,
    '2026-08-02T09:00:00Z'
),
(
    '55555555-5555-4555-8555-555555555555',
    7,
    '2026-07-30T09:00:00Z',
    NULL,
    '2026-07-02T09:00:00Z'
),
(
    '66666666-6666-4666-8666-666666666666',
    3,
    '2026-07-31T09:00:00Z',
    NULL,
    '2026-07-03T09:00:00Z'
),
(
    '77777777-7777-4777-8777-777777777777',
    30,
    '2099-01-02T00:00:00Z',
    NULL,
    '2026-08-04T09:00:00Z'
),
(
    '88888888-8888-4888-8888-888888888888',
    30,
    '2099-01-03T00:00:00Z',
    NULL,
    '2026-08-05T09:00:00Z'
);

COMMIT;
