// The orders API plus the same CPU endpoints as the Node server. The default build resizes with the pure-Go imaging
// library; the "vips" build tag links libvips (what sharp also wraps), kept out of the API tests because linking it adds RAM.
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"image/jpeg"
	"log"
	"net/http"
	"os"
	"runtime"
	"slices"
	"sync"

	"github.com/disintegration/imaging"

	"pvg/api"
)

const (
	sortSize  = 200000
	heavySize = 500000
)

func seededNumbers(size int) []int64 {
	out := make([]int64, size)
	x := int64(12345)
	for i := range out {
		x = (x*1103515245 + 12345) % 2147483648
		out[i] = x
	}
	return out
}

func writeJSON(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(v); err != nil {
		log.Print(err)
	}
}

// Built on first use, so the arrays don't count towards the RAM measured in the orders test.
func sortHandler(size int) http.HandlerFunc {
	numbers := sync.OnceValue(func() []int64 { return seededNumbers(size) })
	return func(w http.ResponseWriter, _ *http.Request) {
		sorted := slices.Clone(numbers())
		slices.Sort(sorted)
		writeJSON(w, map[string]int64{"median": sorted[len(sorted)>>1]})
	}
}

func resizePure(path string) (int, error) {
	img, err := imaging.Open(path)
	if err != nil {
		return 0, err
	}
	var buf bytes.Buffer
	err = jpeg.Encode(&buf, imaging.Resize(img, 800, 0, imaging.Lanczos), &jpeg.Options{Quality: 80})
	return buf.Len(), err
}

// Node's libuv pool caps concurrent resizes by itself; unbounded goroutines would hold every decoded 12 MP photo in RAM at once.
func resizeHandler(resize func(string) (int, error), path string) http.HandlerFunc {
	slots := make(chan struct{}, runtime.GOMAXPROCS(0))
	return func(w http.ResponseWriter, _ *http.Request) {
		slots <- struct{}{}
		defer func() { <-slots }()
		n, err := resize(path)
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		writeJSON(w, map[string]int{"bytes": n})
	}
}

// Set by the vips build.
var nativeResize func(string) (int, error)

func main() {
	s, err := api.NewServer(context.Background(), json.Marshal)
	if err != nil {
		log.Fatal(err)
	}
	resize := nativeResize
	if resize == nil {
		resize = resizePure
	}
	mux := s.Routes()
	mux.HandleFunc("GET /sort", sortHandler(sortSize))
	mux.HandleFunc("GET /heavy", sortHandler(heavySize))
	mux.HandleFunc("GET /resize", resizeHandler(resize, os.Getenv("PHOTO")))
	log.Fatal(http.ListenAndServe(":8000", mux))
}
