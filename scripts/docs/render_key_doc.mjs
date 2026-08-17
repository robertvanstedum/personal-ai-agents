#!/usr/bin/env node

import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

import { marked } from "marked";
import { chromium } from "playwright";

const [sourceArg, outputArg, titleArg, repoArg] = process.argv.slice(2);
if (!sourceArg || !outputArg || !titleArg || !repoArg) {
  console.error(
    "Usage: node render_key_doc.mjs SOURCE.md OUTPUT.pdf TITLE REPO_ROOT",
  );
  process.exit(2);
}

const repoRoot = path.resolve(repoArg);
const sourcePath = path.resolve(sourceArg);
const outputPath = path.resolve(outputArg);
const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const nodeModules = path.join(scriptDir, "node_modules");
const stylesheet = fs.readFileSync(
  path.join(scriptDir, "key-docs.css"),
  "utf8",
);
const markdown = fs.readFileSync(sourcePath, "utf8");
const expectedDiagrams = (markdown.match(/^```mermaid\s*$/gm) || []).length;

marked.setOptions({ gfm: true, breaks: false });
const renderedMarkdown = marked.parse(markdown);

const html = `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${escapeHtml(titleArg)}</title>
  <style>${stylesheet}</style>
</head>
<body>
  <main>${renderedMarkdown}</main>
  <script type="module">
    import mermaid from "/__modules/mermaid/dist/mermaid.esm.min.mjs";
    const codeBlocks = [...document.querySelectorAll("pre > code.language-mermaid")];
    for (const code of codeBlocks) {
      const host = document.createElement("div");
      host.className = "mermaid";
      host.textContent = code.textContent;
      code.parentElement.replaceWith(host);
    }
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      theme: "base",
      flowchart: { htmlLabels: true, curve: "basis", useMaxWidth: true },
      themeVariables: {
        fontFamily: "Avenir Next, Segoe UI, Arial, sans-serif",
        fontSize: "15px",
        primaryColor: "#e7f4f1",
        primaryTextColor: "#173a36",
        primaryBorderColor: "#25776f",
        lineColor: "#6f817c",
        secondaryColor: "#f2ecfa",
        tertiaryColor: "#fff3d6",
        clusterBkg: "#faf8f2",
        clusterBorder: "#cfd8d3",
        edgeLabelBackground: "#fffdf8"
      }
    });
    try {
      await mermaid.run({ querySelector: ".mermaid" });
      for (const link of document.querySelectorAll("a[href]")) {
        const raw = link.getAttribute("href");
        if (!raw || raw.startsWith("#") || /^[a-z]+:/i.test(raw)) continue;
        link.href = new URL(
          raw,
          "https://github.com/robertvanstedum/personal-ai-agents/blob/main/"
        ).href;
      }
      const isVisualBlock = (element) =>
        element.matches("table, figure, .mermaid") ||
        element.querySelector("img, svg");
      for (const heading of [
        ...document.querySelectorAll("h1, h2, h3, h4"),
      ]) {
        const blocks = [heading];
        let cursor = heading.nextElementSibling;
        let foundVisual = false;
        while (
          cursor &&
          !cursor.matches("h1, h2, h3, h4") &&
          blocks.length < 4
        ) {
          blocks.push(cursor);
          if (isVisualBlock(cursor)) {
            foundVisual = true;
            break;
          }
          cursor = cursor.nextElementSibling;
        }
        if (!foundVisual && blocks.length > 2) {
          blocks.splice(2);
        }
        if (blocks.length < 2) continue;
        const group = document.createElement("section");
        group.className = "keep-section-start";
        heading.before(group);
        for (const block of blocks) group.append(block);
      }
      for (const label of [...document.querySelectorAll("p")]) {
        if (label.closest(".keep-section-start")) continue;
        const meaningfulNodes = [...label.childNodes].filter(
          (node) => node.nodeType !== Node.TEXT_NODE || node.textContent.trim()
        );
        const onlyNode = meaningfulNodes[0];
        if (
          meaningfulNodes.length !== 1 ||
          onlyNode.nodeType !== Node.ELEMENT_NODE ||
          onlyNode.tagName !== "STRONG"
        ) {
          continue;
        }
        const visual = label.nextElementSibling;
        if (!visual || !isVisualBlock(visual)) continue;
        const group = document.createElement("section");
        group.className = "keep-section-start";
        label.before(group);
        group.append(label, visual);
      }
      window.__PDF_RENDER_RESULT__ = {
        ok: true,
        diagrams: document.querySelectorAll(".mermaid svg").length,
        keptSectionStarts: document.querySelectorAll(".keep-section-start").length
      };
    } catch (error) {
      window.__PDF_RENDER_RESULT__ = {
        ok: false,
        message: String(error && error.stack ? error.stack : error)
      };
    }
  </script>
</body>
</html>`;

function escapeHtml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function contentType(filePath) {
  const ext = path.extname(filePath).toLowerCase();
  return {
    ".css": "text/css",
    ".gif": "image/gif",
    ".html": "text/html; charset=utf-8",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".js": "text/javascript",
    ".json": "application/json",
    ".mjs": "text/javascript",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
  }[ext] || "application/octet-stream";
}

function safeFile(base, requestPath) {
  const decoded = decodeURIComponent(requestPath).replace(/^\/+/, "");
  const resolved = path.resolve(base, decoded);
  return resolved === base || resolved.startsWith(`${base}${path.sep}`)
    ? resolved
    : null;
}

const server = http.createServer((request, response) => {
  const url = new URL(request.url, "http://127.0.0.1");
  if (url.pathname === "/__document") {
    response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    response.end(html);
    return;
  }

  const modulePrefix = "/__modules/";
  const base = url.pathname.startsWith(modulePrefix) ? nodeModules : repoRoot;
  const relative = url.pathname.startsWith(modulePrefix)
    ? url.pathname.slice(modulePrefix.length)
    : url.pathname;
  const filePath = safeFile(base, relative);
  if (!filePath || !fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) {
    response.writeHead(404);
    response.end("Not found");
    return;
  }
  response.writeHead(200, { "Content-Type": contentType(filePath) });
  fs.createReadStream(filePath).pipe(response);
});

await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const { port } = server.address();
let browser;

try {
  const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined;
  browser = await chromium.launch({ headless: true, executablePath });
  const page = await browser.newPage({
    viewport: { width: 1280, height: 900 },
    deviceScaleFactor: 1,
  });
  await page.goto(`http://127.0.0.1:${port}/__document`, {
    waitUntil: "networkidle",
  });
  await page.waitForFunction(() => window.__PDF_RENDER_RESULT__ !== undefined, {
    timeout: 60_000,
  });
  const result = await page.evaluate(() => window.__PDF_RENDER_RESULT__);
  if (!result.ok) {
    throw new Error(`Mermaid rendering failed: ${result.message}`);
  }
  if (result.diagrams !== expectedDiagrams) {
    throw new Error(
      `Rendered ${result.diagrams} Mermaid diagrams; expected ${expectedDiagrams}`,
    );
  }
  await page.emulateMedia({ media: "print" });
  await page.pdf({
    path: outputPath,
    format: "Letter",
    printBackground: true,
    preferCSSPageSize: true,
    displayHeaderFooter: true,
    headerTemplate: "<span></span>",
    footerTemplate: `
      <div style="width:100%;padding:0 0.64in;color:#73827e;font:8px Arial,sans-serif;display:flex;justify-content:space-between;">
        <span>mini-moi · github.com/robertvanstedum/personal-ai-agents</span>
        <span><span class="pageNumber"></span> / <span class="totalPages"></span></span>
      </div>`,
  });
  console.log(
    JSON.stringify({ diagrams: result.diagrams, expected: expectedDiagrams }),
  );
} finally {
  if (browser) await browser.close();
  await new Promise((resolve) => server.close(resolve));
}
