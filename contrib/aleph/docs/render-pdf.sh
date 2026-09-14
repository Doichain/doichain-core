#!/usr/bin/env bash
# Render the customer hardening-test report (haertetest-kunde.html) to PDF with
# headless Chrome.  No dependencies beyond a Chrome/Chromium install.
#   ./render-pdf.sh
# Override the browser with CHROME=/path/to/chrome (Linux/Windows paths differ).
export LC_ALL=C
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="${HERE}/haertetest-kunde.html"
OUT="${HERE}/haertetest-kunde.pdf"
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"

[ -f "$SRC" ] || { echo "missing $SRC" >&2; exit 1; }
[ -x "$CHROME" ] || { echo "Chrome not found at '$CHROME'; set CHROME=/path/to/chrome" >&2; exit 1; }

"$CHROME" --headless=new --disable-gpu --no-pdf-header-footer \
  --virtual-time-budget=8000 --print-to-pdf="$OUT" "file://$SRC" 2>/dev/null
echo "wrote $OUT"
