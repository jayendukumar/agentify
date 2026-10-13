// Serves the demo build and proxies /api to the test API, so the page and API share one origin.
import { createServer, request } from 'node:http'
import { readFile } from 'node:fs/promises'
import { join, extname, resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
const dist = resolve(process.env.DIST ?? join(dirname(fileURLToPath(import.meta.url)), 'demo-dist'))
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.png': 'image/png', '.svg': 'image/svg+xml', '.woff2': 'font/woff2' }
createServer(async (req, res) => {
  if (req.url.startsWith('/api') || req.url.startsWith('/healthz')) {
    const up = request({ host: '127.0.0.1', port: 8020, path: req.url, method: req.method, headers: req.headers }, r => { res.writeHead(r.statusCode, r.headers); r.pipe(res) })
    up.on('error', e => { res.writeHead(502).end(String(e)) })
    req.pipe(up); return
  }
  try {
    const path = decodeURIComponent(new URL(req.url, 'http://x').pathname)
    const file = extname(path) ? join(dist, path) : join(dist, 'index.html')
    res.setHeader('Content-Type', mime[extname(file)] ?? 'application/octet-stream')
    res.end(await readFile(file))
  } catch { res.writeHead(404).end() }
}).listen(4310, '127.0.0.1', () => console.log('serving on 4310'))
