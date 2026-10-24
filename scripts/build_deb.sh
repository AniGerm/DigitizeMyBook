#!/usr/bin/env bash
# Baut DigitizeMyBook-${VERSION}-amd64.deb unter dist/
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Ungültige VERSION: $VERSION" >&2
  exit 1
fi

if [[ ! -d "$ROOT/frontend/node_modules" ]]; then
  (cd "$ROOT/frontend" && npm install)
fi
(cd "$ROOT/frontend" && npm run build)

STAGE="$ROOT/dist/.deb-staging"
rm -rf "$STAGE"
mkdir -p "$STAGE"
trap 'rm -rf "$STAGE"' EXIT

PKG_ROOT="$STAGE/digitizemybook"
mkdir -p "$PKG_ROOT"
cp -a "$ROOT/packaging/deb/DEBIAN" "$PKG_ROOT/"
cp -a "$ROOT/packaging/deb/lib" "$PKG_ROOT/"
cp -a "$ROOT/packaging/deb/usr" "$PKG_ROOT/"

sed -i "s/__VERSION__/${VERSION}/g" "$PKG_ROOT/DEBIAN/control"
chmod 755 "$PKG_ROOT/DEBIAN/postinst" "$PKG_ROOT/DEBIAN/prerm" "$PKG_ROOT/DEBIAN/postrm"
chmod 755 "$PKG_ROOT/usr/lib/digitizemybook/apply-update.sh"

OPT="$PKG_ROOT/opt/digitizemybook"
mkdir -p "$OPT/backend" "$OPT/frontend"
cp -a "$ROOT/backend/app" "$OPT/backend/app"
cp -a "$ROOT/backend/requirements.txt" "$OPT/backend/requirements.txt"
cp -a "$ROOT/frontend/dist" "$OPT/frontend/dist"
cp -a "$ROOT/VERSION" "$OPT/VERSION"
cp -a "$ROOT/README.md" "$OPT/README.md"
cp -a "$ROOT/start.sh" "$OPT/start.sh"
find "$OPT" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true

mkdir -p "$ROOT/dist"
OUT="$ROOT/dist/DigitizeMyBook-${VERSION}-amd64.deb"
rm -f "$OUT"
dpkg-deb --root-owner-group --build "$PKG_ROOT" "$OUT"
echo "Built: $OUT"
ls -lh "$OUT"
