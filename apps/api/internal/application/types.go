package application

import (
	"errors"
	"time"

	"alrightmate/api/internal/review"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
)

var (
	ErrValidation            = errors.New("validation failed")
	ErrNotFound              = errors.New("not found")
	ErrInvalidTransition     = errors.New("invalid lifecycle transition")
	ErrExpressionConflict    = errors.New("canonical expression already exists")
	ErrReviewAlreadyRecorded = errors.New("review already recorded for Seoul date")
)

type Status string

const (
	StatusDraft     Status = "draft"
	StatusPublished Status = "published"
	StatusArchived  Status = "archived"
)

type Clock interface {
	Now() time.Time
}

type systemClock struct{}

func (systemClock) Now() time.Time {
	return time.Now()
}

type Service struct {
	pool  *pgxpool.Pool
	clock Clock
}

func NewService(pool *pgxpool.Pool, clock Clock) *Service {
	if clock == nil {
		clock = systemClock{}
	}
	return &Service{pool: pool, clock: clock}
}

type OptionalString struct {
	Set   bool
	Value *string
}

type CreateExpressionInput struct {
	Expression string
	MeaningKo  string
	Note       *string
	SourceURL  *string
	Tags       []string
	Status     Status
}

type UpdateExpressionInput struct {
	Expression *string
	MeaningKo  *string
	Note       OptionalString
	SourceURL  OptionalString
	Tags       *[]string
	Status     *Status
}

type ReviewState struct {
	IntervalDays   int16
	DueAt          time.Time
	LastReviewedAt *time.Time
	UpdatedAt      time.Time
}

type Expression struct {
	ID          uuid.UUID
	Expression  string
	MeaningKo   string
	Note        *string
	SourceURL   *string
	Tags        []string
	Status      Status
	CreatedAt   time.Time
	UpdatedAt   time.Time
	PublishedAt *time.Time
	ArchivedAt  *time.Time
	ReviewState *ReviewState
}

type ListPublicExpressionsInput struct {
	Page     int32
	PageSize int32
	Query    string
	Tag      string
}

type PublicExpressionListItem struct {
	ID          uuid.UUID
	Expression  string
	MeaningKo   string
	Tags        []string
	SourceURL   string
	PublishedAt time.Time
}

type PublicExpressionPage struct {
	Items    []PublicExpressionListItem
	Page     int32
	PageSize int32
	Total    int64
}

type PublicExpressionDetail struct {
	ID          uuid.UUID
	Expression  string
	MeaningKo   string
	Note        *string
	Tags        []string
	SourceURL   string
	PublishedAt time.Time
}

type DueReviewExpression struct {
	ID          uuid.UUID
	Expression  string
	MeaningKo   string
	Note        *string
	Tags        []string
	SourceURL   string
	Status      Status
	PublishedAt time.Time
}

type DueReview struct {
	Expression  DueReviewExpression
	ReviewState ReviewState
}

type ReviewEventResult struct {
	EventID              uuid.UUID
	ExpressionID         uuid.UUID
	Rating               review.Rating
	ReviewedAt           time.Time
	ReviewDateSeoul      string
	PreviousIntervalDays int16
	NextIntervalDays     int16
	ReviewState          ReviewState
}

type ProgressDay struct {
	Date  string
	Total int32
	Again int32
	Good  int32
}

type ReviewProgress struct {
	Days      int32
	StartDate string
	EndDate   string
	Total     int32
	Entries   []ProgressDay
}
