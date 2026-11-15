// Renders docs/REPORT.md's companions to PDF and PNG with Playwright's Chromium.
//   node docs/build/render.mjs mermaid <in.mmd> <out.png>
//   node docs/build/render.mjs pdf <in.html> <out.pdf>
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { resolve } from "node:path";

// Playwright is a dev dependency of the web app; use that install.
const { chromium } = createRequire(new URL("../../apps/web/package.json", import.meta.url))("playwright");

const [mode, input, output] = process.argv.slice(2);
const browser = await chromium.launch();
const page = await browser.newPage({ deviceScaleFactor: 2 });
if (mode === "mermaid") {
  const src = readFileSync(input, "utf8").replace(/^---[\s\S]*?---\s*/, "");
  await page.setContent(`<!doctype html><html><body style="margin:0;background:#fff">
    <pre class="mermaid">${src.replace(/</g, "&lt;")}</pre>
    <script src="https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.min.js"></script>
    <script>mermaid.initialize({ startOnLoad: true, theme: "neutral", flowchart: { curve: "linear" } });</script>
    </body></html>`);
  await page.waitForSelector("pre.mermaid svg", { timeout: 30000 });
  await (await page.$("pre.mermaid svg")).screenshot({ path: output });
} else if (mode === "pdf") {
  await page.goto("file://" + resolve(input));
  await page.pdf({ path: output, format: "A4", printBackground: true,
                   margin: { top: "14mm", bottom: "14mm", left: "15mm", right: "15mm" },
                   displayHeaderFooter: true, headerTemplate: "<span></span>",
                   footerTemplate: '<div style="font-size:8px;width:100%;text-align:center;color:#666">' +
                     '<span class="pageNumber"></span> / <span class="totalPages"></span></div>' });
}
await browser.close();
