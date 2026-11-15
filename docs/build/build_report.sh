#!/usr/bin/env bash
# docs/REPORT.md -> docs/REPORT.pdf: pandoc to HTML, then Chromium (Playwright) prints it.
# Also re-renders the graphs from the Mermaid files `python -m agent.graphs.render` writes.
set -euo pipefail
cd "$(dirname "$0")/../.."
for g in bid_graph conversation_graph; do
  node docs/build/render.mjs mermaid "docs/images/$g.mmd" "docs/images/$g.png"
done
pandoc docs/REPORT.md -s --css build/report.css --metadata lang=en -o docs/REPORT.html
node docs/build/render.mjs pdf docs/REPORT.html docs/REPORT.pdf
rm -f docs/REPORT.html
echo "docs/REPORT.pdf: $(python3 -c "import re;print(len(re.findall(rb'/Type\s*/Page[^s]', open('docs/REPORT.pdf','rb').read())))") pages"
