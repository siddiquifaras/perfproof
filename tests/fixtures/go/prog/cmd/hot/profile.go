package main

import (
	"fmt"
	"os"
	"runtime"
	"runtime/pprof"
)

// startProfile writes a pprof profile to $PERFPROOF_PPROF when it is
// set, of the kind named by $PERFPROOF_PPROF_KIND (cpu, mem, mutex or
// block; cpu when empty). The function it returns stops the profile
// and writes the file. With the variable unset it does nothing, so the
// same program is the one that is timed.
func startProfile() func() {
	path := os.Getenv("PERFPROOF_PPROF")
	if path == "" {
		return func() {}
	}
	file, err := os.Create(path)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
	write := func(name string) func() {
		return func() {
			if err := pprof.Lookup(name).WriteTo(file, 0); err != nil {
				fmt.Fprintln(os.Stderr, err)
			}
			file.Close()
		}
	}
	switch os.Getenv("PERFPROOF_PPROF_KIND") {
	case "", "cpu":
		if err := pprof.StartCPUProfile(file); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(2)
		}
		return func() {
			pprof.StopCPUProfile()
			file.Close()
		}
	case "mem":
		runtime.MemProfileRate = 4096
		return write("allocs")
	case "mutex":
		runtime.SetMutexProfileFraction(1)
		return write("mutex")
	case "block":
		runtime.SetBlockProfileRate(1)
		return write("block")
	}
	fmt.Fprintln(os.Stderr, "PERFPROOF_PPROF_KIND must be cpu, mem, mutex or block")
	os.Exit(2)
	return nil
}
