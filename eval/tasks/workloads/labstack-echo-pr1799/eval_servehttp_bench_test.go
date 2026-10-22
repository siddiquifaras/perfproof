package echo

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

type evalDiscard struct{ header http.Header }

func (w *evalDiscard) Header() http.Header         { return w.header }
func (w *evalDiscard) Write(p []byte) (int, error) { return len(p), nil }
func (w *evalDiscard) WriteHeader(int)             {}

// BenchmarkEvalServeHTTPPath serves GET requests whose path has one
// percent-encoded segment, through the router, so every request goes
// through the code that picks the path ServeHTTP routes on.
func BenchmarkEvalServeHTTPPath(b *testing.B) {
	e := New()
	h := func(c Context) error { return c.NoContent(http.StatusOK) }
	e.GET("/api/v1/users/:name/orders/:order/items/:item", h)
	e.GET("/api/v1/users/:name/orders", h)
	e.GET("/api/v1/health", h)
	req := httptest.NewRequest(
		http.MethodGet,
		"/api/v1/users/john%20doe/orders/987654/items/42",
		nil,
	)
	w := &evalDiscard{header: http.Header{}}
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		e.ServeHTTP(w, req)
	}
}
