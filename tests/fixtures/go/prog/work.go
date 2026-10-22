// Package prog is a small program that profiles show something in:
// quadratic string building, a map that grows without a size hint, and
// goroutines that wait on one lock.
package prog

import (
	"strconv"
	"sync"
)

// Join concatenates the parts with a comma, building the result one
// piece at a time.
func Join(parts []string) string {
	out := ""
	for i, p := range parts {
		if i > 0 {
			out += ","
		}
		out += p
	}
	return out
}

// Count returns how many times each word occurs.
func Count(words []string) map[string]int {
	counts := map[string]int{}
	for _, w := range words {
		counts[w]++
	}
	return counts
}

// Words returns n distinct words.
func Words(n int) []string {
	words := make([]string, n)
	for i := range words {
		words[i] = "w" + strconv.Itoa(i)
	}
	return words
}

// Contend has workers goroutines add to one total, each holding one
// lock around every addition.
func Contend(workers, additions int) int {
	var (
		mu    sync.Mutex
		wg    sync.WaitGroup
		total int
	)
	for w := 0; w < workers; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := 0; i < additions; i++ {
				mu.Lock()
				total += i & 1
				mu.Unlock()
			}
		}()
	}
	wg.Wait()
	return total
}

// Sign returns -1, 0 or 1.
func Sign(n int) int {
	if n < 0 {
		return -1
	}
	if n == 0 {
		return 0
	}
	return 1
}

// Unused is called by no test.
func Unused() string {
	return "unused"
}

// Clamp returns n, or zero when n is negative.
func Clamp(n int) int {
	if n < 0 { return 0 }
	return n
}
