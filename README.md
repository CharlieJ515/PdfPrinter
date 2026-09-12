# pdfprinter

A small Qt6 GUI for printing PDFs on Linux (X11 and Wayland).

Shows a preview of the PDF (via QtPdf) and submits the file directly to
CUPS with `lp`, so the PDF is never re-rasterized on the client side.

## Features

- PDF preview (multi-page, fit-to-width)
- Printer selection with system default preselected
- Copies, page ranges (`1-4,7`), duplex, color/grayscale, paper size
- Drag & drop, `application/pdf` desktop integration
- Works natively on both Wayland and X11 (Qt picks the right backend)

## Requirements

- Python ≥ 3.12
- PyQt6 (with QtPdf)
- CUPS (`lp` / `lpstat` command-line tools)

## Run from source

```sh
python -m pdfprinter.main [file.pdf]
```

## Install (Arch Linux)

```sh
makepkg -si
```

## License

MIT
