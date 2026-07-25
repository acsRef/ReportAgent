const http = require('node:http')
const fs = require('node:fs')
const path = require('node:path')

const root = process.argv[2] || process.cwd()
const port = Number(process.argv[3] || 8765)
const types = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.md': 'text/markdown; charset=utf-8',
  '.png': 'image/png',
  '.json': 'application/json; charset=utf-8',
}

http
  .createServer(async (req, res) => {
    try {
      const url = new URL(req.url, `http://${req.headers.host}`)
      const requested = url.pathname === '/' ? '/atelier/index.html' : decodeURIComponent(url.pathname)
      const file = path.resolve(root, `.${requested}`)
      if (!file.startsWith(path.resolve(root))) {
        res.writeHead(403)
        res.end('Forbidden')
        return
      }
      const data = await fs.promises.readFile(file)
      res.writeHead(200, { 'Content-Type': types[path.extname(file)] || 'application/octet-stream' })
      res.end(data)
    } catch (err) {
      res.writeHead(404)
      res.end('Not found')
    }
  })
  .listen(port, '127.0.0.1', () => {
    console.log(`dev server: http://127.0.0.1:${port}/atelier/index.html`)
  })
