#!/bin/sh
# Re-render docs/architecture.png from docs/architecture.html (CSS layout, JS wires).
# Usage: sh tools/render_arch.sh   (requires chromium)
cd "$(dirname "$0")/.."
/usr/local/bin/chromium --headless=new --disable-gpu --no-sandbox \
  --screenshot=docs/architecture.png --window-size=960,470 \
  --force-device-scale-factor=2 --virtual-time-budget=2000 \
  --hide-scrollbars "file://$PWD/docs/architecture.html" 2>/dev/null
echo "docs/architecture.png written"
