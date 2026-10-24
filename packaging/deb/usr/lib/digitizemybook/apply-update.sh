#!/bin/bash
# Installiert ein DigitizeMyBook-*.deb und startet den Dienst neu.
# Aufruf: pkexec /usr/lib/digitizemybook/apply-update.sh /pfad/zum.deb
set -euo pipefail
DEB="${1:-}"
if [[ -z "$DEB" || ! -f "$DEB" ]]; then
  echo "Usage: $0 /path/to/DigitizeMyBook-VERSION-amd64.deb" >&2
  exit 2
fi
case "$(basename "$DEB")" in
  DigitizeMyBook-*.deb) ;;
  *)
    echo "Nur DigitizeMyBook-*.deb erlaubt." >&2
    exit 2
    ;;
esac
export DEBIAN_FRONTEND=noninteractive
if command -v apt-get >/dev/null 2>&1; then
  apt-get install -y --reinstall "$DEB" || dpkg -i "$DEB"
else
  dpkg -i "$DEB"
fi
systemctl daemon-reload || true
systemctl restart digitizemybook || true
echo "digitizeMyBook-Update installiert."
