package structx

import (
	"context"
	"math"
	"math/rand"
	"strconv"
	"time"
)

// RetryPolicy controls retry behavior for transient failures. Mirrors the
// Python/Node SDKs' RetryPolicy.
//
// Defaults are conservative: 3 attempts total, exponential backoff capped at
// 30s, 5xx-retry enabled for reads but disabled for writes (the client
// enforces the write-exclusion at call sites; RetryOn5xx only affects reads).
// RespectRetryAfter lets the server tell us when to come back — used by 429s
// and transient 503s.
type RetryPolicy struct {
	// MaxAttempts is the total number of attempts, including the first one.
	// A value of 3 means 1 initial try + up to 2 retries.
	MaxAttempts int
	// InitialBackoff is the base delay before the first retry.
	InitialBackoff time.Duration
	// MaxBackoff caps any single backoff delay.
	MaxBackoff time.Duration
	// RetryOn5xx enables retrying 5xx responses on read paths.
	RetryOn5xx bool
	// RespectRetryAfter honors the server's Retry-After header when present.
	RespectRetryAfter bool
	// Jitter is the fraction (0..1) of a backoff to randomize by, so
	// simultaneous clients don't thunder-herd on retry.
	Jitter float64
}

// DefaultRetryPolicy returns the SDK's default retry policy.
func DefaultRetryPolicy() RetryPolicy {
	return RetryPolicy{
		MaxAttempts:       3,
		InitialBackoff:    1 * time.Second,
		MaxBackoff:        30 * time.Second,
		RetryOn5xx:        true,
		RespectRetryAfter: true,
		Jitter:            0.2,
	}
}

// backoffFor returns the delay before attempt (1-indexed). If the server sent
// Retry-After (retryAfter >= 0), that value is used — caps still apply.
// Otherwise it is exponential backoff with bounded jitter.
func (p RetryPolicy) backoffFor(attempt int, retryAfter time.Duration) time.Duration {
	if retryAfter >= 0 && p.RespectRetryAfter {
		if retryAfter > p.MaxBackoff {
			return p.MaxBackoff
		}
		return retryAfter
	}
	base := float64(p.InitialBackoff) * math.Pow(2, float64(attempt-1))
	jitterRange := base * p.Jitter
	// rand.Float64() in [0,1) -> offset in [-jitterRange, +jitterRange).
	offset := (rand.Float64()*2 - 1) * jitterRange
	d := time.Duration(base + offset)
	if d > p.MaxBackoff {
		return p.MaxBackoff
	}
	if d < 0 {
		return 0
	}
	return d
}

// writePaths are endpoints that mutate / bill — they are retried only on
// transport errors, never on a 5xx (the server may have partially processed
// before failing, and re-sending could double-bill). Matches the Node SDK's
// WRITE_PATHS, which additionally includes /v1/synth/generate over Python's
// set (synth generation is a billed write).
var writePaths = map[string]struct{}{
	"/v1/extract":          {},
	"/v1/extract/batch":    {},
	"/v1/schemas/infer":    {},
	"/v1/synth/generate":   {},
	"/v1/extract/document": {},
}

// isRetryableResponse reports whether a 5xx response on path should be retried
// under policy. Writes never retry on 5xx; 501 Not Implemented is a permanent
// rejection, not transient.
func isRetryableResponse(path string, status int, policy RetryPolicy) bool {
	if !policy.RetryOn5xx {
		return false
	}
	if _, isWrite := writePaths[path]; isWrite {
		return false
	}
	return status >= 500 && status < 600 && status != 501
}

// parseRetryAfter parses a Retry-After header value (seconds form). Returns a
// negative duration when missing or malformed. The HTTP-date form is not
// accepted — no endpoint emits it today, and it would need timezone
// arithmetic we'd rather avoid (same choice as the Node SDK).
func parseRetryAfter(value string) time.Duration {
	if value == "" {
		return -1
	}
	secs, err := strconv.ParseFloat(value, 64)
	if err != nil || math.IsNaN(secs) || math.IsInf(secs, 0) || secs < 0 {
		return -1
	}
	return time.Duration(secs * float64(time.Second))
}

// sleepWithContext waits for d or until ctx is cancelled, whichever comes
// first. It returns ctx.Err() if the context ends during the wait.
func sleepWithContext(ctx context.Context, d time.Duration) error {
	if d <= 0 {
		return ctx.Err()
	}
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-t.C:
		return nil
	}
}
