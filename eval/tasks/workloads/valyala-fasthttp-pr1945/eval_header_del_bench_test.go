package fasthttp

import (
	"fmt"
	"testing"
)

// BenchmarkEvalHeaderDel adds several values under each of ten custom
// header names, then deletes every name, as a proxy that strips headers
// would.
func BenchmarkEvalHeaderDel(b *testing.B) {
	keys := make([]string, 10)
	for i := range keys {
		keys[i] = fmt.Sprintf("X-Item-%d", i)
	}
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		var h RequestHeader
		for r := 0; r < 4; r++ {
			for _, k := range keys {
				h.Add(k, "val")
			}
		}
		for _, k := range keys {
			h.Del(k)
		}
	}
}
