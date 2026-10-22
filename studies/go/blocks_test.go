package echo

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"runtime"
	"testing"
	"time"
)

type blocksDiscard struct{ header http.Header }

func (w *blocksDiscard) Header() http.Header         { return w.header }
func (w *blocksDiscard) Write(p []byte) (int, error) { return len(p), nil }
func (w *blocksDiscard) WriteHeader(int)             {}

// TestEvalBlocks serves the same request in 60 blocks of 100000
// iterations and prints, for each block, the time per iteration and
// the garbage collections that ran during it. It runs in a fresh
// process each time, so the first block is the cold one.
func TestEvalBlocks(t *testing.T) {
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
	w := &blocksDiscard{header: http.Header{}}
	const blocks, iterations = 60, 100000
	for b := 0; b < blocks; b++ {
		var before, after runtime.MemStats
		runtime.ReadMemStats(&before)
		start := time.Now()
		for i := 0; i < iterations; i++ {
			e.ServeHTTP(w, req)
		}
		elapsed := time.Since(start)
		runtime.ReadMemStats(&after)
		fmt.Printf("block %d %.2f %d\n", b, float64(elapsed.Nanoseconds())/iterations,
			after.NumGC-before.NumGC)
	}
}
