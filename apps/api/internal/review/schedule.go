package review

import (
	"fmt"
	"time"
)

type Rating string

const (
	RatingAgain Rating = "again"
	RatingGood  Rating = "good"
)

func NextInterval(current int16, rating Rating) (int16, error) {
	if !isSupportedInterval(current) {
		return 0, fmt.Errorf("unsupported review interval: %d", current)
	}

	switch rating {
	case RatingAgain:
		return 1, nil
	case RatingGood:
		switch current {
		case 1:
			return 3, nil
		case 3:
			return 7, nil
		case 7:
			return 14, nil
		case 14, 30:
			return 30, nil
		}
	}

	return 0, fmt.Errorf("unsupported review rating: %q", rating)
}

func NextDueAt(reviewedAt time.Time, intervalDays int16) (time.Time, error) {
	if !isSupportedInterval(intervalDays) {
		return time.Time{}, fmt.Errorf("unsupported review interval: %d", intervalDays)
	}

	return reviewedAt.UTC().Add(time.Duration(intervalDays) * 24 * time.Hour), nil
}

func isSupportedInterval(intervalDays int16) bool {
	switch intervalDays {
	case 1, 3, 7, 14, 30:
		return true
	default:
		return false
	}
}
