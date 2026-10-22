package prog

import "testing"

func TestJoin(t *testing.T) {
	if got := Join([]string{"a", "b", "c"}); got != "a,b,c" {
		t.Fatalf("Join = %q", got)
	}
}

func TestCount(t *testing.T) {
	if got := Count([]string{"x", "y", "x"}); got["x"] != 2 {
		t.Fatalf("Count = %v", got)
	}
}

func TestSignPositive(t *testing.T) {
	if Sign(5) != 1 {
		t.Fatal("Sign(5)")
	}
}

func TestClamp(t *testing.T) {
	if Clamp(5) != 5 {
		t.Fatal("Clamp(5)")
	}
}

func BenchmarkJoin(b *testing.B) {
	words := Words(2000)
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		Join(words)
	}
}

func BenchmarkCount(b *testing.B) {
	words := Words(20000)
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		Count(words)
	}
}

func BenchmarkContend(b *testing.B) {
	for i := 0; i < b.N; i++ {
		Contend(8, 20000)
	}
}
