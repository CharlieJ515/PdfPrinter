# Maintainer: Charlie Jang <charlie.jang515@gmail.com>
pkgname=pdfprinter
pkgver=0.1.0
pkgrel=1
pkgdesc="Qt6 GUI for printing PDFs via CUPS (X11 and Wayland)"
arch=('any')
url="https://github.com/CharlieJ515/PdfPrinter"
license=('MIT')
depends=('python' 'python-pyqt6' 'cups' 'ghostscript')
optdepends=('qpdf: page-range preview fallback when cups pdftopdf is unavailable')
makedepends=('python-build' 'python-installer' 'python-wheel' 'python-setuptools')
# For local development builds; replace with a tarball/git source when
# publishing to the AUR, e.g.:
#   source=("$pkgname-$pkgver.tar.gz::$url/archive/v$pkgver.tar.gz")
source=()
sha256sums=()

build() {
  cd "$startdir"
  python -m build --wheel --no-isolation --outdir "$srcdir/dist"
}

package() {
  cd "$startdir"
  python -m installer --destdir="$pkgdir" "$srcdir"/dist/*.whl
  install -Dm644 data/pdfprinter.desktop \
    "$pkgdir/usr/share/applications/pdfprinter.desktop"
  install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
