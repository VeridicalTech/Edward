#!/bin/sh
# Re-render all README figures from their HTML sources (CSS layout, chromium @2x).
cd "$(dirname "$0")/.."
for spec in "architecture:960,470" "bench-eir-cost:960,400" "bench-families:960,340" "bench-external:960,340"; do
  name="${spec%%:*}"; size="${spec##*:}"
  /usr/local/bin/chromium --headless=new --disable-gpu --no-sandbox \
    --screenshot="docs/$name.png" --window-size="$size" \
    --force-device-scale-factor=2 --virtual-time-budget=2000 --hide-scrollbars \
    "file://$PWD/docs/$name.html" 2>/dev/null
  echo "docs/$name.png"
done
