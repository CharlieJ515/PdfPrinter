# Maintainer: Charlie Jang <charlie.jang515@gmail.com>
pkgname=pdfprinter
pkgver=0.1.0
pkgrel=1
pkgdesc="Qt6 GUI for printing PDFs via CUPS (X11 and Wayland)"
arch=('any')
url="https://github.com/CharlieJ515/PdfPrinter"
license=('MIT')
depends=('python' 'python-pyqt6' 'python-pikepdf' 'cups' 'ghostscript')
optdepends=('qpdf: page-range preview fallback when cups pdftopdf is unavailable')
makedepends=('python-build' 'python-installer' 'python-wheel' 'python-setuptools')
source=("$pkgname-$pkgver.tar.gz::$url/archive/v$pkgver.tar.gz")
sha256sums=('c7e81997d2c63eefd7167c9d428d09e4279d78c17c938c375b8f610e5418a3f8')

build() {
  cd "PdfPrinter-$pkgver"
  python -m build --wheel --no-isolation
}

package() {
  cd "PdfPrinter-$pkgver"
  python -m installer --destdir="$pkgdir" dist/*.whl
  install -Dm644 data/pdfprinter.desktop \
    "$pkgdir/usr/share/applications/pdfprinter.desktop"
  install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
