package application

import (
	"fmt"
	"net/url"
	"regexp"
	"sort"
	"strings"
	"unicode/utf8"
)

var tagPattern = regexp.MustCompile(`^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$`)

type normalisedCreateInput struct {
	expression string
	meaningKo  string
	note       *string
	sourceURL  *string
	tags       []string
	status     Status
}

func normaliseCreateInput(input CreateExpressionInput) (normalisedCreateInput, error) {
	status := input.Status
	if status == "" {
		status = StatusDraft
	}
	if status == StatusArchived {
		return normalisedCreateInput{}, fmt.Errorf("%w: cannot create archived expression", ErrInvalidTransition)
	}
	if status != StatusDraft && status != StatusPublished {
		return normalisedCreateInput{}, fmt.Errorf("%w: invalid status", ErrValidation)
	}

	expression, err := requiredText("expression", input.Expression, 300)
	if err != nil {
		return normalisedCreateInput{}, err
	}
	meaningKo, err := requiredText("meaningKo", input.MeaningKo, 1000)
	if err != nil {
		return normalisedCreateInput{}, err
	}
	if err := validateNote(input.Note); err != nil {
		return normalisedCreateInput{}, err
	}
	if err := validateSourceURL(input.SourceURL); err != nil {
		return normalisedCreateInput{}, err
	}
	if status == StatusPublished && input.SourceURL == nil {
		return normalisedCreateInput{}, fmt.Errorf("%w: published expression requires sourceUrl", ErrValidation)
	}
	tags, err := normaliseTags(input.Tags)
	if err != nil {
		return normalisedCreateInput{}, err
	}

	return normalisedCreateInput{
		expression: expression,
		meaningKo:  meaningKo,
		note:       input.Note,
		sourceURL:  input.SourceURL,
		tags:       tags,
		status:     status,
	}, nil
}

func requiredText(field, value string, maximum int) (string, error) {
	normalised := strings.TrimSpace(value)
	if normalised == "" {
		return "", fmt.Errorf("%w: %s is required", ErrValidation, field)
	}
	if utf8.RuneCountInString(normalised) > maximum {
		return "", fmt.Errorf("%w: %s exceeds %d characters", ErrValidation, field, maximum)
	}
	return normalised, nil
}

func validateNote(note *string) error {
	if note != nil && utf8.RuneCountInString(*note) > 4000 {
		return fmt.Errorf("%w: note exceeds 4000 characters", ErrValidation)
	}
	return nil
}

func validateSourceURL(sourceURL *string) error {
	if sourceURL == nil {
		return nil
	}
	if utf8.RuneCountInString(*sourceURL) > 2048 {
		return fmt.Errorf("%w: sourceUrl exceeds 2048 characters", ErrValidation)
	}
	parsed, err := url.Parse(*sourceURL)
	if err != nil || !parsed.IsAbs() || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Hostname() == "" || parsed.User != nil {
		return fmt.Errorf("%w: sourceUrl must be an absolute HTTP(S) URL without credentials", ErrValidation)
	}
	return nil
}

func normaliseTags(tags []string) ([]string, error) {
	unique := make(map[string]struct{}, len(tags))
	for _, tag := range tags {
		normalised := strings.ToLower(strings.TrimSpace(tag))
		if !tagPattern.MatchString(normalised) {
			return nil, fmt.Errorf("%w: invalid tag %q", ErrValidation, tag)
		}
		unique[normalised] = struct{}{}
	}
	if len(unique) > 20 {
		return nil, fmt.Errorf("%w: at most 20 tags are allowed", ErrValidation)
	}
	normalised := make([]string, 0, len(unique))
	for tag := range unique {
		normalised = append(normalised, tag)
	}
	sort.Strings(normalised)
	return normalised, nil
}

func normaliseOptionalQuery(query string) (*string, error) {
	normalised := strings.TrimSpace(query)
	if normalised == "" {
		return nil, nil
	}
	if utf8.RuneCountInString(normalised) > 200 {
		return nil, fmt.Errorf("%w: q exceeds 200 characters", ErrValidation)
	}
	return &normalised, nil
}

func normaliseOptionalTag(tag string) (*string, error) {
	if tag == "" {
		return nil, nil
	}
	tags, err := normaliseTags([]string{tag})
	if err != nil {
		return nil, err
	}
	return &tags[0], nil
}
