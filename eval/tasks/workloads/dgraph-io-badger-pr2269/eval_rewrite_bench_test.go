package badger

import (
	"fmt"
	"math/rand"
	"os"
	"testing"
	"time"
)

// BenchmarkEvalRewriteExpired times one value log rewrite of a file
// whose entries have all expired.
func BenchmarkEvalRewriteExpired(b *testing.B) {
	for n := 0; n < b.N; n++ {
		b.StopTimer()
		dir, err := os.MkdirTemp("", "badger-eval")
		if err != nil {
			b.Fatal(err)
		}
		opt := getTestOptions(dir)
		opt.ValueLogFileSize = 1 << 22
		opt.BaseTableSize = 1 << 15
		opt.ValueThreshold = 1 << 10
		kv, err := Open(opt)
		if err != nil {
			b.Fatal(err)
		}
		rng := rand.New(rand.NewSource(1))
		txn := kv.NewTransaction(true)
		for i := 0; i < 8000; i++ {
			v := make([]byte, 2<<10)
			rng.Read(v)
			e := NewEntry([]byte(fmt.Sprintf("key%06d", i)), v).
				WithTTL(time.Millisecond)
			if err := txn.SetEntry(e); err != nil {
				b.Fatal(err)
			}
			if i%100 == 99 {
				if err := txn.Commit(); err != nil {
					b.Fatal(err)
				}
				txn = kv.NewTransaction(true)
			}
		}
		if err := txn.Commit(); err != nil {
			b.Fatal(err)
		}
		time.Sleep(50 * time.Millisecond)
		kv.vlog.filesLock.RLock()
		lf := kv.vlog.filesMap[kv.vlog.sortedFids()[0]]
		kv.vlog.filesLock.RUnlock()
		b.StartTimer()
		if err := kv.vlog.rewrite(lf); err != nil {
			b.Fatal(err)
		}
		b.StopTimer()
		kv.Close()
		removeDir(dir)
	}
}
