package review

import (
	"testing"
	"time"
)

func TestNextInterval(t *testing.T) {
	tests := []struct {
		name    string
		current int16
		rating  Rating
		want    int16
		wantErr bool
	}{
		// Catches a mutation that lets "again" advance any supported interval.
		{name: "again resets 1 to 1", current: 1, rating: RatingAgain, want: 1},
		{name: "again resets 3 to 1", current: 3, rating: RatingAgain, want: 1},
		{name: "again resets 7 to 1", current: 7, rating: RatingAgain, want: 1},
		{name: "again resets 14 to 1", current: 14, rating: RatingAgain, want: 1},
		{name: "again resets 30 to 1", current: 30, rating: RatingAgain, want: 1},
		// Catches mutations in the approved good-rating progression.
		{name: "good advances 1 to 3", current: 1, rating: RatingGood, want: 3},
		{name: "good advances 3 to 7", current: 3, rating: RatingGood, want: 7},
		{name: "good advances 7 to 14", current: 7, rating: RatingGood, want: 14},
		{name: "good advances 14 to 30", current: 14, rating: RatingGood, want: 30},
		{name: "good keeps 30 at 30", current: 30, rating: RatingGood, want: 30},
		// Catches mutations that accept an interval outside the state machine.
		{name: "rejects unsupported current interval for good", current: 2, rating: RatingGood, wantErr: true},
		{name: "rejects unsupported current interval for again", current: 2, rating: RatingAgain, wantErr: true},
		// Catches mutations that silently treat an unknown rating as a known rating.
		{name: "rejects unsupported rating", current: 1, rating: Rating("easy"), wantErr: true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, err := NextInterval(tt.current, tt.rating)
			if tt.wantErr {
				if err == nil {
					t.Fatal("NextInterval() error = nil, want non-nil")
				}
				return
			}
			if err != nil {
				t.Fatalf("NextInterval() error = %v, want nil", err)
			}
			if got != tt.want {
				t.Fatalf("NextInterval() = %d, want %d", got, tt.want)
			}
		})
	}
}

func TestNextDueAt(t *testing.T) {
	reviewedAt := time.Date(2026, time.March, 7, 23, 30, 0, 0, time.FixedZone("KST", 9*60*60))

	tests := []struct {
		name         string
		intervalDays int16
		want         time.Time
		wantErr      bool
	}{
		// Catches mutations that use calendar days, map intervals incorrectly, or preserve a non-UTC location.
		{name: "adds 1 exact 24-hour day in UTC", intervalDays: 1, want: time.Date(2026, time.March, 8, 14, 30, 0, 0, time.UTC)},
		{name: "adds 3 exact 24-hour days in UTC", intervalDays: 3, want: time.Date(2026, time.March, 10, 14, 30, 0, 0, time.UTC)},
		{name: "adds 7 exact 24-hour days in UTC", intervalDays: 7, want: time.Date(2026, time.March, 14, 14, 30, 0, 0, time.UTC)},
		{name: "adds 14 exact 24-hour days in UTC", intervalDays: 14, want: time.Date(2026, time.March, 21, 14, 30, 0, 0, time.UTC)},
		{name: "adds 30 exact 24-hour days in UTC", intervalDays: 30, want: time.Date(2026, time.April, 6, 14, 30, 0, 0, time.UTC)},
		// Catches mutations that accept an interval outside the review state machine.
		{name: "rejects zero interval", intervalDays: 0, wantErr: true},
		{name: "rejects unsupported interval", intervalDays: 2, wantErr: true},
		{name: "rejects negative interval", intervalDays: -1, wantErr: true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, err := NextDueAt(reviewedAt, tt.intervalDays)
			if tt.wantErr {
				if err == nil {
					t.Fatal("NextDueAt() error = nil, want non-nil")
				}
				return
			}
			if err != nil {
				t.Fatalf("NextDueAt() error = %v, want nil", err)
			}
			if !got.Equal(tt.want) {
				t.Fatalf("NextDueAt() = %s, want %s", got, tt.want)
			}
			if got.Location() != time.UTC {
				t.Fatalf("NextDueAt() location = %s, want UTC", got.Location())
			}
		})
	}
}
