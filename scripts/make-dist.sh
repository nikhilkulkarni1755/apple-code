#!/bin/sh
# Build everything a release needs into dist/: a .tar.gz (for the curl installer), a .zip (to download), and the npm package.
set -eu
cd "$(dirname "$0")/.."

VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' agent/__init__.py)
NPM_VERSION=$(node -p "require('./npm/package.json').version")
[ "$VERSION" = "$NPM_VERSION" ] || { echo "version mismatch: agent/__init__.py says $VERSION, npm/package.json says $NPM_VERSION" >&2; exit 1; }

NAME="apple-code-$VERSION"
rm -rf dist npm/payload
mkdir -p dist

stage() { # folder: the source a release needs, without tests, caches, or the compiled (machine specific) helper
  mkdir -p "$1"
  rsync -a --exclude __pycache__ --exclude '*.pyc' --exclude fm-helper --exclude .DS_Store agent "$1/"
  cp pyproject.toml install.sh README.md "$1/"
}

stage "dist/$NAME"
tar -czf "dist/$NAME.tar.gz" -C dist "$NAME"
cp scripts/Install.command "dist/$NAME/Install.command"
chmod +x "dist/$NAME/Install.command" "dist/$NAME/install.sh"
(cd dist && zip -qr "$NAME.zip" "$NAME")
rm -rf "dist/$NAME"

stage npm/payload
cp README.md npm/README.md
(cd npm && npm pack --silent --pack-destination ../dist >/dev/null)
rm -rf npm/payload npm/README.md

(cd dist && shasum -a 256 -- * > SHA256SUMS)
echo "built in dist/:"; ls dist
