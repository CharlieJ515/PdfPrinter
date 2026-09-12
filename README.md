# PDF Printer

A Qt6 GUI for printing PDFs on Linux (X11 and Wayland) with a preview
that shows **exactly what will come out of the printer** — the preview
is produced by the same CUPS filter (`pdftopdf`) and the same
transforms that build the print job.

Built with binder printing in mind: mirrored binding margins, a punch
hole guide, and content placement measured from each page's actual ink.

## Features

- **True print preview** — page ranges, odd/even, n-up, orientation,
  reverse order, scaling and margins are all previewed by running the
  real transform pipeline, not simulated. Rendering happens in the
  background; the UI never blocks.
- **Print options from the driver** — quality, paper source and media
  type list the selected printer's actual choices, with its defaults
  marked; unsupported options (like duplex on a printer without a
  duplex unit) are grayed out.
- **Margins & binder printing**
  - Placement modes: shrink to fit the margins, shift at original size,
    or center the measured content beside an 18 mm punch line (with or
    without shrinking).
  - Mirror margins for double-sided binder printing — even pages get
    the binding clearance on the other side, following the final
    printed page order.
  - Screen-only guides: red dashed margin lines and a blue dashed
    punch-hole line (never printed).
- **Zotero integration** — browse your Zotero library with its
  collections tree and title/creator metadata, search across title,
  authors and filename, and print directly from storage.
- **Robust printing** — structurally broken PDFs (common with
  publisher downloads) are repaired automatically before they reach
  the printer's filter chain; jobs are verified against the server
  filter before submission and tracked in the CUPS queue until they
  complete.
- **Comfort** — pinch-to-zoom and inertial scrolling, one-click reuse
  of the last print's settings, collapsible advanced options with an
  active-settings counter, grayscale preview.

## Requirements

- Python ≥ 3.12, PyQt6 (with QtPdf), pikepdf
- CUPS (`lp`/`lpstat`) with `pdftopdf` (cups-filters)
- Ghostscript (PDF repair, fallback transforms)
- qpdf (optional fallback)

## Install (Arch Linux)

```sh
makepkg -si
```

## Run from source

```sh
python -m pdfprinter.main [file.pdf]
```

## License

MIT
