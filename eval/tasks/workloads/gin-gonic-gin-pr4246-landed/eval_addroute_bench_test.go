package gin

import (
	"fmt"
	"testing"
)

// BenchmarkEvalAddRoutes registers a few hundred routes with
// parameters and wildcards on a new engine, which goes through the
// radix tree's insertion.
func BenchmarkEvalAddRoutes(b *testing.B) {
	var paths []string
	for i := 0; i < 60; i++ {
		paths = append(paths,
			fmt.Sprintf("/api/v1/resource%d", i),
			fmt.Sprintf("/api/v1/resource%d/:id", i),
			fmt.Sprintf("/api/v1/resource%d/:id/items/:item", i),
			fmt.Sprintf("/api/v1/resource%d/:id/files/*path", i),
		)
	}
	h := func(*Context) {}
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		e := New()
		for _, p := range paths {
			e.GET(p, h)
		}
	}
}
