#!/usr/bin/env bash
# One command: builds ATL + all its libraries into usr/ and packages the click (self-contained, nothing is
# downloaded on the phone). Needs Docker and clickable (pip install clickable-ut).
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
"$HERE/tools/build_atl.sh"
cd "$HERE"
clickable build --skip-review
echo "Check the package: it must contain usr/ and must NOT contain atl/ (source)"
ls -lh build/*/app/*.click 2>/dev/null || find . -name '*.click' -maxdepth 4 -exec ls -lh {} +
