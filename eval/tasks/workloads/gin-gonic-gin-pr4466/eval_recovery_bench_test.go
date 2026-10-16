package gin

import (
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
)

// BenchmarkEvalPanicRecovery serves a handler that panics behind the
// Recovery middleware, which formats a stack trace on every request.
func BenchmarkEvalPanicRecovery(b *testing.B) {
	SetMode(ReleaseMode)
	router := New()
	router.Use(RecoveryWithWriter(io.Discard))
	router.GET("/", func(c *Context) { panic("eval") })
	req, _ := http.NewRequest(http.MethodGet, "/", nil)
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		router.ServeHTTP(httptest.NewRecorder(), req)
	}
}
