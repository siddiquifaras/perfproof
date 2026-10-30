package logrus

import (
	"io/ioutil"
	"testing"
)

// BenchmarkEvalLogger logs lines with fields through a Logger that writes
// to a discarding sink, once with the text and once with the JSON formatter.
func BenchmarkEvalLogger(b *testing.B) {
	for _, tc := range []struct {
		name      string
		formatter Formatter
	}{
		{"text", &TextFormatter{DisableColors: true}},
		{"json", &JSONFormatter{}},
	} {
		b.Run(tc.name, func(b *testing.B) {
			logger := New()
			logger.Out = ioutil.Discard
			logger.Formatter = tc.formatter
			b.ReportAllocs()
			b.ResetTimer()
			for i := 0; i < b.N; i++ {
				logger.WithFields(Fields{"foo": "bar", "n": i}).Info("a log line")
				logger.Info("a plain log line")
			}
		})
	}
}
