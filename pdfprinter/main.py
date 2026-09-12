"""Entry point for pdfprinter."""

from __future__ import annotations

import argparse
import sys

from PyQt6.QtWidgets import QApplication

from . import __version__
from .window import MainWindow


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="pdfprinter", description="Print PDFs via CUPS with a preview GUI"
    )
    parser.add_argument("pdf", nargs="?", help="PDF file to open")
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    args, qt_args = parser.parse_known_args()

    app = QApplication(sys.argv[:1] + qt_args)
    app.setApplicationName("pdfprinter")
    app.setDesktopFileName("pdfprinter")

    window = MainWindow(args.pdf)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
