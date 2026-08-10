package testcontract

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"unicode"
)

func TestInitialMigrationMatchesDataModelContract(t *testing.T) {
	raw := readMigration(t)
	up := migrationSection(t, raw, "-- +goose Up", "-- +goose Down")
	statements := sqlStatements(up)
	tables := createTableStatements(statements)
	if countCreateTableStatements(statements) != 4 || len(tables) != 4 {
		t.Fatalf("CREATE TABLE statements = %d (%d distinct), want exactly 4 distinct tables: %v", countCreateTableStatements(statements), len(tables), tables)
	}

	expressions := tableStatement(t, tables, "expressions")
	assertTableColumns(t, "expressions", expressions, []columnContract{
		{name: "id", definition: "id uuid primary key default gen_random_uuid()"},
		{name: "expression", definition: "expression text not null"},
		{name: "meaning_ko", definition: "meaning_ko text not null"},
		{name: "note", definition: "note text"},
		{name: "source_url", definition: "source_url text"},
		{name: "status", definition: "status text not null"},
		{name: "created_at", definition: "created_at timestamptz not null"},
		{name: "updated_at", definition: "updated_at timestamptz not null"},
		{name: "published_at", definition: "published_at timestamptz"},
		{name: "archived_at", definition: "archived_at timestamptz"},
	})
	assertStatement(t, "expressions", expressions, []string{
		"constraint expressions_expression_trimmed_nonempty_check check (expression = btrim(expression) and expression <> '')",
		"constraint expressions_meaning_ko_trimmed_nonempty_check check (meaning_ko = btrim(meaning_ko) and meaning_ko <> '')",
		"constraint expressions_status_check check (status in ('draft', 'published', 'archived'))",
		"constraint expressions_lifecycle_timestamps_check check ( (status = 'draft' and published_at is null and archived_at is null) or (status = 'published' and published_at is not null and archived_at is null) or (status = 'archived' and published_at is not null and archived_at is not null) )",
		"constraint expressions_source_url_format_check check ( source_url is null or ( btrim(source_url) <> '' and source_url ~* '^https?://[^/?#[:space:]]+(?:[/?#][^[:space:]]*)?$' ) )",
		"constraint expressions_published_source_url_check check ( status = 'draft' or source_url is not null )",
	})

	expressionTags := tableStatement(t, tables, "expression_tags")
	assertTableColumns(t, "expression_tags", expressionTags, []columnContract{
		{name: "expression_id", definition: "expression_id uuid not null"},
		{name: "tag", definition: "tag text not null"},
	})
	assertStatement(t, "expression_tags", expressionTags, []string{
		"primary key (expression_id, tag)",
		"constraint expression_tags_expression_id_fkey foreign key (expression_id) references expressions(id) on delete restrict",
		"constraint expression_tags_tag_check check ( tag = btrim(tag) and tag = lower(tag) and tag ~ '^[a-z0-9]([a-z0-9-]{0,38}[a-z0-9])?$' )",
	})

	reviewState := tableStatement(t, tables, "expression_review_state")
	assertTableColumns(t, "expression_review_state", reviewState, []columnContract{
		{name: "expression_id", definition: "expression_id uuid primary key"},
		{name: "interval_days", definition: "interval_days smallint not null"},
		{name: "due_at", definition: "due_at timestamptz not null"},
		{name: "last_reviewed_at", definition: "last_reviewed_at timestamptz"},
		{name: "updated_at", definition: "updated_at timestamptz not null"},
	})
	assertStatement(t, "expression_review_state", reviewState, []string{
		"constraint expression_review_state_expression_id_fkey foreign key (expression_id) references expressions(id) on delete restrict",
		"constraint expression_review_state_interval_days_check check (interval_days in (1, 3, 7, 14, 30))",
	})

	reviewEvents := tableStatement(t, tables, "review_events")
	assertTableColumns(t, "review_events", reviewEvents, []columnContract{
		{name: "id", definition: "id uuid primary key default gen_random_uuid()"},
		{name: "expression_id", definition: "expression_id uuid not null"},
		{name: "rating", definition: "rating text not null"},
		{name: "reviewed_at", definition: "reviewed_at timestamptz not null"},
		{name: "review_date_seoul", definition: "review_date_seoul date generated always as ((reviewed_at at time zone 'Asia/Seoul')::date) stored not null"},
		{name: "previous_interval_days", definition: "previous_interval_days smallint not null"},
		{name: "next_interval_days", definition: "next_interval_days smallint not null"},
		{name: "created_at", definition: "created_at timestamptz not null"},
	})
	assertStatement(t, "review_events", reviewEvents, []string{
		"constraint review_events_expression_id_fkey foreign key (expression_id) references expressions(id) on delete restrict",
		"constraint review_events_rating_check check (rating in ('again', 'good'))",
		"constraint review_events_previous_interval_days_check check (previous_interval_days in (1, 3, 7, 14, 30))",
		"constraint review_events_next_interval_days_check check (next_interval_days in (1, 3, 7, 14, 30))",
		"constraint review_events_expression_review_date_seoul_key unique (expression_id, review_date_seoul)",
	})

	for _, definition := range []string{
		"create unique index expressions_lower_expression_key on expressions (lower(expression))",
		"create index expressions_public_browse_idx on expressions (status, published_at desc, id desc)",
		"create index expressions_search_idx on expressions using gin (to_tsvector('simple', expression || ' ' || meaning_ko || ' ' || coalesce(note, '')))",
		"create index expression_tags_tag_expression_id_idx on expression_tags (tag, expression_id)",
		"create index expression_review_state_due_at_expression_id_idx on expression_review_state (due_at, expression_id)",
		"create index review_events_review_date_seoul_expression_id_idx on review_events (review_date_seoul, expression_id)",
		"create index review_events_expression_id_reviewed_at_idx on review_events (expression_id, reviewed_at desc)",
	} {
		assertExactStatement(t, statements, definition)
	}

	down := sqlStatements(migrationSection(t, raw, "-- +goose Down", ""))
	wantDown := []string{
		"drop table review_events",
		"drop table expression_review_state",
		"drop table expression_tags",
		"drop table expressions",
	}
	if len(down) != len(wantDown) {
		t.Fatalf("down statements = %v, want %v", down, wantDown)
	}
	for i, want := range wantDown {
		if down[i] != want {
			t.Errorf("down statement %d = %q, want %q", i+1, down[i], want)
		}
	}
}

func readMigration(t *testing.T) string {
	t.Helper()
	path := filepath.Join("..", "..", "db", "migrations", "00001_initial.sql")
	contents, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read migration %q: %v", path, err)
	}
	return string(contents)
}

func migrationSection(t *testing.T, migration, startMarker, endMarker string) string {
	t.Helper()
	start := strings.Index(migration, startMarker)
	if start < 0 {
		t.Fatalf("migration is missing %q", startMarker)
	}
	section := migration[start+len(startMarker):]
	if endMarker == "" {
		return section
	}
	end := strings.Index(section, endMarker)
	if end < 0 {
		t.Fatalf("migration is missing %q", endMarker)
	}
	return section[:end]
}

func sqlStatements(sql string) []string {
	var statements []string
	for _, statement := range splitSQLStatements(stripLineComments(sql)) {
		statement = normalizeSQL(statement)
		if statement != "" {
			statements = append(statements, statement)
		}
	}
	return statements
}

func stripLineComments(sql string) string {
	var result strings.Builder
	inSingle, inDouble, inComment := false, false, false
	for i := 0; i < len(sql); i++ {
		current := sql[i]
		if inComment {
			if current == '\n' {
				inComment = false
				result.WriteByte(current)
			}
			continue
		}
		if inSingle {
			result.WriteByte(current)
			if current == '\'' {
				if i+1 < len(sql) && sql[i+1] == '\'' {
					i++
					result.WriteByte(sql[i])
				} else {
					inSingle = false
				}
			}
			continue
		}
		if inDouble {
			result.WriteByte(current)
			if current == '"' {
				if i+1 < len(sql) && sql[i+1] == '"' {
					i++
					result.WriteByte(sql[i])
				} else {
					inDouble = false
				}
			}
			continue
		}
		if current == '-' && i+1 < len(sql) && sql[i+1] == '-' {
			inComment = true
			i++
			continue
		}
		result.WriteByte(current)
		switch current {
		case '\'':
			inSingle = true
		case '"':
			inDouble = true
		}
	}
	return result.String()
}

func splitSQLStatements(sql string) []string {
	var statements []string
	start := 0
	inSingle, inDouble := false, false
	for i := 0; i < len(sql); i++ {
		current := sql[i]
		if inSingle {
			if current == '\'' {
				if i+1 < len(sql) && sql[i+1] == '\'' {
					i++
				} else {
					inSingle = false
				}
			}
			continue
		}
		if inDouble {
			if current == '"' {
				if i+1 < len(sql) && sql[i+1] == '"' {
					i++
				} else {
					inDouble = false
				}
			}
			continue
		}
		switch current {
		case '\'':
			inSingle = true
		case '"':
			inDouble = true
		case ';':
			statements = append(statements, sql[start:i])
			start = i + 1
		}
	}
	return append(statements, sql[start:])
}

func normalizeSQL(sql string) string {
	var normalized strings.Builder
	inSingle, inDouble, pendingSpace := false, false, false
	for i := 0; i < len(sql); i++ {
		current := sql[i]
		if inSingle {
			normalized.WriteByte(current)
			if current == '\'' {
				if i+1 < len(sql) && sql[i+1] == '\'' {
					i++
					normalized.WriteByte(sql[i])
				} else {
					inSingle = false
				}
			}
			continue
		}
		if inDouble {
			normalized.WriteByte(current)
			if current == '"' {
				if i+1 < len(sql) && sql[i+1] == '"' {
					i++
					normalized.WriteByte(sql[i])
				} else {
					inDouble = false
				}
			}
			continue
		}
		if unicode.IsSpace(rune(current)) {
			pendingSpace = normalized.Len() > 0
			continue
		}
		if pendingSpace {
			normalized.WriteByte(' ')
			pendingSpace = false
		}
		switch current {
		case '\'':
			normalized.WriteByte(current)
			inSingle = true
		case '"':
			normalized.WriteByte(current)
			inDouble = true
		default:
			if current >= 'A' && current <= 'Z' {
				current += 'a' - 'A'
			}
			normalized.WriteByte(current)
		}
	}
	return normalized.String()
}

type columnContract struct {
	name       string
	definition string
}

func createTableStatements(statements []string) map[string]string {
	tables := make(map[string]string)
	for _, statement := range statements {
		fields := strings.Fields(statement)
		if len(fields) >= 4 && fields[0] == "create" && fields[1] == "table" {
			tables[fields[2]] = statement
		}
	}
	return tables
}

func countCreateTableStatements(statements []string) int {
	count := 0
	for _, statement := range statements {
		fields := strings.Fields(statement)
		if len(fields) >= 4 && fields[0] == "create" && fields[1] == "table" {
			count++
		}
	}
	return count
}

func tableStatement(t *testing.T, tables map[string]string, name string) string {
	t.Helper()
	statement, ok := tables[name]
	if !ok {
		t.Fatalf("missing CREATE TABLE %q", name)
	}
	return statement
}

func assertTableColumns(t *testing.T, tableName, statement string, expected []columnContract) {
	t.Helper()
	actual, err := parseTableColumns(statement)
	if err != nil {
		t.Fatalf("parse CREATE TABLE %q: %v", tableName, err)
	}
	if len(actual) != len(expected) {
		t.Errorf("table %q has %d columns, want %d: %v", tableName, len(actual), len(expected), actual)
	}
	for _, want := range expected {
		got, ok := actual[want.name]
		if !ok {
			t.Errorf("table %q is missing column %q", tableName, want.name)
			continue
		}
		if got != want.definition {
			t.Errorf("table %q column %q = %q, want %q", tableName, want.name, got, want.definition)
		}
	}
	for name := range actual {
		if !containsColumn(expected, name) {
			t.Errorf("table %q has unexpected column %q", tableName, name)
		}
	}
}

func parseTableColumns(statement string) (map[string]string, error) {
	start := strings.Index(statement, "(")
	if start < 0 {
		return nil, fmt.Errorf("opening parenthesis not found")
	}
	end, err := matchingParenthesis(statement, start)
	if err != nil {
		return nil, err
	}
	if strings.TrimSpace(statement[end+1:]) != "" {
		return nil, fmt.Errorf("unexpected content after table definition")
	}

	columns := make(map[string]string)
	for _, clause := range splitTopLevelClauses(statement[start+1 : end]) {
		if isTableConstraint(clause) {
			continue
		}
		fields := strings.Fields(clause)
		if len(fields) < 2 {
			return nil, fmt.Errorf("invalid column clause %q", clause)
		}
		name := fields[0]
		if _, exists := columns[name]; exists {
			return nil, fmt.Errorf("duplicate column %q", name)
		}
		columns[name] = clause
	}
	return columns, nil
}

func matchingParenthesis(sql string, start int) (int, error) {
	depth := 0
	inSingle, inDouble := false, false
	for i := start; i < len(sql); i++ {
		current := sql[i]
		if inSingle {
			if current == '\'' {
				if i+1 < len(sql) && sql[i+1] == '\'' {
					i++
				} else {
					inSingle = false
				}
			}
			continue
		}
		if inDouble {
			if current == '"' {
				if i+1 < len(sql) && sql[i+1] == '"' {
					i++
				} else {
					inDouble = false
				}
			}
			continue
		}
		switch current {
		case '\'':
			inSingle = true
		case '"':
			inDouble = true
		case '(':
			depth++
		case ')':
			depth--
			if depth == 0 {
				return i, nil
			}
		}
	}
	return 0, fmt.Errorf("closing parenthesis not found")
}

func splitTopLevelClauses(sql string) []string {
	var clauses []string
	start, depth := 0, 0
	inSingle, inDouble := false, false
	for i := 0; i < len(sql); i++ {
		current := sql[i]
		if inSingle {
			if current == '\'' {
				if i+1 < len(sql) && sql[i+1] == '\'' {
					i++
				} else {
					inSingle = false
				}
			}
			continue
		}
		if inDouble {
			if current == '"' {
				if i+1 < len(sql) && sql[i+1] == '"' {
					i++
				} else {
					inDouble = false
				}
			}
			continue
		}
		switch current {
		case '\'':
			inSingle = true
		case '"':
			inDouble = true
		case '(':
			depth++
		case ')':
			depth--
		case ',':
			if depth == 0 {
				clauses = append(clauses, strings.TrimSpace(sql[start:i]))
				start = i + 1
			}
		}
	}
	if last := strings.TrimSpace(sql[start:]); last != "" {
		clauses = append(clauses, last)
	}
	return clauses
}

func isTableConstraint(clause string) bool {
	return strings.HasPrefix(clause, "constraint ") ||
		strings.HasPrefix(clause, "primary key") ||
		strings.HasPrefix(clause, "foreign key") ||
		strings.HasPrefix(clause, "unique") ||
		strings.HasPrefix(clause, "check ") ||
		strings.HasPrefix(clause, "exclude ")
}

func containsColumn(columns []columnContract, name string) bool {
	for _, column := range columns {
		if column.name == name {
			return true
		}
	}
	return false
}

func assertStatement(t *testing.T, tableName, statement string, required []string) {
	t.Helper()
	for _, fragment := range required {
		if !strings.Contains(statement, fragment) {
			t.Errorf("table %q is missing semantic fragment %q", tableName, fragment)
		}
	}
}

func assertExactStatement(t *testing.T, statements []string, want string) {
	t.Helper()
	for _, statement := range statements {
		if statement == want {
			return
		}
	}
	t.Errorf("missing exact statement %q", want)
}
