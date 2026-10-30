package conn

import (
	"testing"
)

// BenchmarkEvalRead writes 4 MiB through one ALTS record connection and
// reads it back from its peer with gRPC's default 16 KiB buffer.
func BenchmarkEvalRead(b *testing.B) {
	const total = 4 << 20
	chunk := make([]byte, 64<<10)
	buf := make([]byte, 16<<10)
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		clientConn, serverConn := newConnPair(rekeyRecordProtocol, nil, nil)
		for sent := 0; sent < total; sent += len(chunk) {
			if _, err := clientConn.Write(chunk); err != nil {
				b.Fatal(err)
			}
		}
		for got := 0; got < total; {
			n, err := serverConn.Read(buf)
			if err != nil {
				b.Fatal(err)
			}
			got += n
		}
	}
}
