CREATE TABLE IF NOT EXISTS expressions (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    phrase text NOT NULL CHECK (char_length(phrase) BETWEEN 1 AND 120),
    meaning text NOT NULL CHECK (char_length(meaning) BETWEEN 1 AND 500)
);
