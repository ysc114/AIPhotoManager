# Super-resolution model

`superres_epoch100-44c6958e.pth` is the PyTorch super-resolution example's
trained x3 model, distributed by the PyTorch project at:

https://s3.amazonaws.com/pytorch/test_data/export/superres_epoch100-44c6958e.pth

SHA-256: `44c6958ea9df3886ef120bad1fc827ffb913e4e6f667b24dc7aea9b032b6df75`

It is loaded only when image super-resolution is requested. Inference runs on
CPU in tiles to keep it separate from the photo-analysis GPU models. The x2
option scales the x3 model output down with Lanczos; it is not a separate x2
model. Originals are read-only; results are written to `photos/` and tracked
in `cache/super_resolution.json`.
