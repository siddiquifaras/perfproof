package echo

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

type evalUser struct {
	ID    int      `json:"id"`
	Name  string   `json:"name"`
	Email string   `json:"email"`
	Tags  []string `json:"tags"`
}

// BenchmarkEvalBindJSON serves a POST whose handler binds a JSON body,
// which goes through DefaultJSONSerializer.Deserialize.
func BenchmarkEvalBindJSON(b *testing.B) {
	e := New()
	e.POST("/users", func(c *Context) error {
		u := new(evalUser)
		if err := c.Bind(u); err != nil {
			return err
		}
		return c.NoContent(http.StatusOK)
	})
	body := `{"id":1,"name":"Jon Snow","email":"jon@example.com","tags":["a","b","c"]}`
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		req := httptest.NewRequest(http.MethodPost, "/users", strings.NewReader(body))
		req.Header.Set(HeaderContentType, MIMEApplicationJSON)
		e.ServeHTTP(httptest.NewRecorder(), req)
	}
}
