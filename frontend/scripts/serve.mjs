// Tiny static server for local development: `npm run dev`, then open
// http://localhost:4173. Serves this folder as-is (config.js points at a
// local API on :8000) and maps /privacy to privacy.html like Vercel does.

import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, join, normalize, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const root = normalize(join(dirname(fileURLToPath(import.meta.url)), ".."));
const port = Number(process.env.PORT || 4173);
const types = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".svg": "image/svg+xml",
  ".woff2": "font/woff2",
  ".txt": "text/plain; charset=utf-8",
};

async function load(path) {
  const file = normalize(join(root, path));
  if (!file.startsWith(root)) throw new Error("outside root");
  return [file, await readFile(file)];
}

createServer(async (req, res) => {
  const path = decodeURIComponent(new URL(req.url, "http://x").pathname);
  const candidates = path === "/" ? ["/index.html"] : [path, `${path}.html`];
  for (const candidate of candidates) {
    try {
      const [file, body] = await load(candidate);
      res.writeHead(200, { "Content-Type": types[extname(file)] || "application/octet-stream" });
      res.end(body);
      return;
    } catch {
      /* try the next candidate */
    }
  }
  const [, body] = await load("/404.html");
  res.writeHead(404, { "Content-Type": types[".html"] });
  res.end(body);
}).listen(port, () => console.log(`APIWatch frontend on http://localhost:${port}`));
