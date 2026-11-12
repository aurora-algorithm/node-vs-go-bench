//go:build vips

package main

import (
	"sync"

	"github.com/davidbyttow/govips/v2/vips"
)

func init() { nativeResize = resizeVips }

// Started on first use, like sharp in Node, so the image library doesn't count towards the orders test's RAM.
var startVips = sync.OnceFunc(func() {
	vips.LoggingSettings(nil, vips.LogLevelError)
	vips.Startup(nil)
})

func resizeVips(path string) (int, error) {
	startVips()
	img, err := vips.NewThumbnailFromFile(path, 800, 100000, vips.InterestingNone)
	if err != nil {
		return 0, err
	}
	defer img.Close()
	params := vips.NewJpegExportParams()
	params.Quality = 80
	out, _, err := img.ExportJpeg(params)
	return len(out), err
}
