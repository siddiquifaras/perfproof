// Command webgo is the web-go trace target: net/http, standard library
// only. It mirrors targets/web/app.py: /health, POST /echo, GET
// /cpu/<n> (sha256 chain), GET /db/<customer> (count and sum of 20000
// orders indexed by customer; an in-memory index, because the Python
// and Node targets use SQLite and Go has none in its standard library).
// Usage: webgo <port>
package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"math"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"syscall"
)

var amounts [500][]float64

func seed() {
	state := uint32(20261201)
	next := func() uint32 {
		state = state*1103515245 + 12345
		return state
	}
	for i := 0; i < 20000; i++ {
		c := next() % 500
		amounts[c] = append(amounts[c], float64(next()%1000000)/10000)
	}
}

func send(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(body)
}

func main() {
	seed()
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", func(w http.ResponseWriter, r *http.Request) {
		send(w, 200, map[string]string{"status": "ok"})
	})
	mux.HandleFunc("POST /echo", func(w http.ResponseWriter, r *http.Request) {
		var body map[string]any
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			send(w, 422, map[string]string{"detail": err.Error()})
			return
		}
		send(w, 200, map[string]any{"keys": len(body), "body": body})
	})
	mux.HandleFunc("GET /cpu/{n}", func(w http.ResponseWriter, r *http.Request) {
		n, err := strconv.Atoi(r.PathValue("n"))
		if err != nil {
			send(w, 422, map[string]string{"detail": "bad n"})
			return
		}
		h := []byte("seed")
		for i := 0; i < n; i++ {
			s := sha256.Sum256(h)
			h = s[:]
		}
		send(w, 200, map[string]string{"digest": hex.EncodeToString(h)})
	})
	mux.HandleFunc("GET /db/{customer}", func(w http.ResponseWriter, r *http.Request) {
		c, err := strconv.Atoi(r.PathValue("customer"))
		if err != nil {
			send(w, 422, map[string]string{"detail": "bad customer"})
			return
		}
		rows := amounts[c%500]
		total := 0.0
		for _, a := range rows {
			total += a
		}
		send(w, 200, map[string]any{
			"orders": len(rows),
			"total":  math.Round(total*100) / 100,
		})
	})
	srv := &http.Server{Addr: "127.0.0.1:" + os.Args[1], Handler: mux}
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGTERM)
	go func() {
		<-stop
		_ = srv.Shutdown(context.Background())
	}()
	if err := srv.ListenAndServe(); err != http.ErrServerClosed {
		os.Exit(1)
	}
}
