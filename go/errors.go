// Typed error taxonomy for the struct-x Go SDK.
//
// Every backend error response carries {"error": string, "code": string}
// (see backend/routers/*.py — convention). The SDK maps HTTP status to a
// sentinel error so callers can use errors.Is, and exposes the rich
// details (status, machine-readable code, request id, redacted body,
// rate-limit info) via *APIError with errors.As.
//
// Hierarchy (mirrors the Python SDK's _exceptions.py):
//
//	ErrStructX                    base; matches anything from the SDK
//	  ErrAPI                      server returned an error response
//	    ErrAuthentication         401 — fix your API key
//	    ErrPermissionDenied       403 — your key works but lacks scope
//	    ErrNotFound               404 — wrong slug or id
//	    ErrValidation             400 / 422 — fix your input
//	    ErrRateLimit              429 — back off; carries credits info
//	    ErrServer                 5xx — retry or contact support
//	  ErrTransport                network/timeout, never reached the server
package structx

import (
	"errors"
	"fmt"
	"strings"
)

// ErrStructX is the base sentinel: errors.Is(err, ErrStructX) is true for
// every error this SDK returns (API errors, transport errors, and the
// config error from NewClient).
var ErrStructX = errors.New("structx")

// Category sentinels. Each wraps a broader category so errors.Is chains:
// e.g. errors.Is(err, ErrRateLimit) implies errors.Is(err, ErrAPI) implies
// errors.Is(err, ErrStructX).
var (
	// ErrAPI matches any error the server returned as an error response.
	ErrAPI = fmt.Errorf("api error: %w", ErrStructX)
	// ErrTransport matches network-level failures (DNS, TLS, timeout,
	// connection refused) where the request never reached the server or
	// the response never made it back.
	ErrTransport = fmt.Errorf("transport error: %w", ErrStructX)

	// ErrAuthentication is a 401 — the API key is missing, malformed, or revoked.
	ErrAuthentication = fmt.Errorf("authentication failed (401): %w", ErrAPI)
	// ErrPermissionDenied is a 403 — the key is valid but lacks scope.
	ErrPermissionDenied = fmt.Errorf("permission denied (403): %w", ErrAPI)
	// ErrNotFound is a 404 — template slug, key id, or other resource is missing.
	ErrNotFound = fmt.Errorf("not found (404): %w", ErrAPI)
	// ErrValidation is a 400 or 422 — the request payload is malformed.
	ErrValidation = fmt.Errorf("validation failed (400/422): %w", ErrAPI)
	// ErrRateLimit is a 429 — the daily credit cap or a per-window request
	// cap was hit. Use errors.As to read RetryAfter / CreditsRemaining.
	ErrRateLimit = fmt.Errorf("rate limited (429): %w", ErrAPI)
	// ErrServer is a 5xx — the backend returned a server-side failure.
	ErrServer = fmt.Errorf("server error (5xx): %w", ErrAPI)
)

// APIError is returned whenever the server responds with a non-2xx status.
//
// Match a category with errors.Is (e.g. errors.Is(err, ErrRateLimit)); pull
// the details with errors.As(err, &apiErr).
//
// Privacy posture: ResponseBody has sensitive keys redacted at construction
// time (see redactBody). Code that logs the error via Error() never sees the
// body at all — a second layer of defense against credential/PII leaks in
// customer logs (Sentry, Datadog, etc.). Reach the (redacted) body via the
// ResponseBody field when programmatic inspection is genuinely needed.
type APIError struct {
	// Message is the human-readable message from the backend's "error" field.
	Message string
	// StatusCode is the HTTP status code.
	StatusCode int
	// Code is the machine-readable code from the backend's "code" field ("" if absent).
	Code string
	// RequestID is the value of the x-request-id (or request-id) response header.
	RequestID string
	// ResponseBody is the parsed error body with sensitive keys redacted.
	ResponseBody map[string]any

	// RetryAfter is the Retry-After header value in seconds, if present (429/503).
	RetryAfter *float64
	// CreditsUsed / CreditsRemaining are populated for credit-exhaustion 429s.
	CreditsUsed      *int
	CreditsRemaining *int

	// kind is the category sentinel that drives errors.Is.
	kind error
}

// Error implements error. It intentionally omits ResponseBody.
func (e *APIError) Error() string {
	bits := []string{fmt.Sprintf("status=%d", e.StatusCode)}
	if e.Code != "" {
		bits = append(bits, fmt.Sprintf("code=%q", e.Code))
	}
	if e.RequestID != "" {
		bits = append(bits, fmt.Sprintf("request_id=%q", e.RequestID))
	}
	return fmt.Sprintf("structx: %s (%s)", e.Message, strings.Join(bits, ", "))
}

// Unwrap chains to the category sentinel so errors.Is climbs the hierarchy
// (ErrRateLimit -> ErrAPI -> ErrStructX).
func (e *APIError) Unwrap() error { return e.kind }

// TransportError is returned when the request never reached the server or the
// response never made it back (DNS failure, connection refused, TLS error,
// timeout, context cancellation). It wraps the underlying cause: both
// errors.Is(err, ErrTransport) and errors.Is(err, context.DeadlineExceeded)
// (etc.) work.
type TransportError struct {
	Message string
	cause   error
}

// Error implements error.
func (e *TransportError) Error() string {
	return "structx: transport error: " + e.Message
}

// Unwrap exposes the underlying cause for errors.Is / errors.As against the
// original network error.
func (e *TransportError) Unwrap() error { return e.cause }

// Is reports a match against the ErrTransport and ErrStructX sentinels.
func (e *TransportError) Is(target error) bool {
	return target == ErrTransport || target == ErrStructX
}

// kindForStatus maps an HTTP status code to its category sentinel.
func kindForStatus(status int) error {
	switch status {
	case 401:
		return ErrAuthentication
	case 403:
		return ErrPermissionDenied
	case 404:
		return ErrNotFound
	case 400, 422:
		return ErrValidation
	case 429:
		return ErrRateLimit
	default:
		if status >= 500 {
			return ErrServer
		}
		return ErrAPI
	}
}

// redactedKeys are keys whose values are stripped from a response body before
// it is attached to an APIError. Matches the Python/Node SDKs 1:1. Comparison
// is case-insensitive.
var redactedKeys = map[string]struct{}{
	"x-api-key":     {},
	"authorization": {},
	"apikey":        {},
	"api_key":       {},
	"password":      {},
	"token":         {},
	"secret":        {},
	"private_key":   {},
	"content":       {},
}

// redactValue returns a length-preserving sentinel for a redacted value so
// debugging can see "this field had data" without seeing what.
func redactValue(v any) string {
	switch s := v.(type) {
	case string:
		return fmt.Sprintf("<redacted %d chars>", len(s))
	case []byte:
		return fmt.Sprintf("<redacted %d bytes>", len(s))
	default:
		return "<redacted>"
	}
}

// redactBody returns a shallow copy of body with sensitive keys' values
// replaced by a redacted sentinel. Defends against callers logging the whole
// error (which would otherwise leak credentials or extraction payloads).
func redactBody(body map[string]any) map[string]any {
	if body == nil {
		return nil
	}
	out := make(map[string]any, len(body))
	for k, v := range body {
		if _, ok := redactedKeys[strings.ToLower(k)]; ok {
			out[k] = redactValue(v)
		} else {
			out[k] = v
		}
	}
	return out
}
