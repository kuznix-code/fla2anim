# Maintainer: Kuznix <kuznix@users.noreply.github.com>
pkgname=fla2anim
pkgver=0.1.0
pkgrel=1
pkgdesc="Convert XFL-based Flash projects to Pencil2D, Synfig, SWF and image sequences"
arch=('any')
url="https://github.com/kuznix-code/fla2anim"
license=('MIT')
depends=('python>=3.10' 'python-pillow')
makedepends=('git' 'python-build' 'python-installer' 'python-setuptools')
source=("git+https://github.com/kuznix-code/fla2anim.git#tag=v${pkgver}")
sha256sums=('SKIP')

build() {
  cd "${srcdir}/${pkgname}"
  python -m build --wheel --no-isolation
}

package() {
  cd "${srcdir}/${pkgname}"
  python -m installer --destdir="${pkgdir}" dist/*.whl
  install -Dm644 LICENSE "${pkgdir}/usr/share/licenses/${pkgname}/LICENSE"
}
