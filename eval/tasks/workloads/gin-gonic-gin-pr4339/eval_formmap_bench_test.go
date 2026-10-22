package gin

import (
	"fmt"
	"net/http/httptest"
	"strings"
	"testing"
)

// BenchmarkEvalQueryMap reads one map ("ids[key]=value") out of a
// query string that carries many other parameters, some with names
// that start like the map's. The query is parsed once, before the
// timer, so the loop measures the map extraction.
func BenchmarkEvalQueryMap(b *testing.B) {
	var query strings.Builder
	for i := 0; i < 150; i++ {
		fmt.Fprintf(&query, "field%d[a]=1&other%d=2&ids%d[x]=3&", i, i, i)
	}
	query.WriteString("ids[a]=1&ids[b]=2&ids[c]=3&ids[]=4&ids[d]=5")
	req := httptest.NewRequest("GET", "/?"+query.String(), nil)
	c := &Context{Request: req}
	if m, ok := c.GetQueryMap("ids"); !ok || len(m) != 4 {
		b.Fatalf("unexpected map %v", m)
	}
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		c.GetQueryMap("ids")
	}
}
