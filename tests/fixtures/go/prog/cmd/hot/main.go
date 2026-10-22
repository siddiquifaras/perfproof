// Command hot runs the prog workload as a program, for the tests of
// the pprof wrapper. profile.go is the helper skills/perfproof/go.md
// gives a program that has no `go test` to carry the profile flags.
package main

import (
	"fmt"

	"example.com/prog"
)

func main() {
	defer startProfile()()
	words := prog.Words(2000)
	n := 0
	for i := 0; i < 400; i++ {
		n += len(prog.Join(words))
		n += len(prog.Count(words))
	}
	n += prog.Contend(8, 20000)
	fmt.Println(n)
}
