// Captures the process twin deck's screenshots from the running app.
//
// 1. Run the stack and seed it:  .\start.ps1 -Demo -ResetDemo -SeedProcessTwin
// 2. Start a headless browser with a throwaway profile:
//      msedge --headless=new --remote-debugging-port=9333 --user-data-dir=<temp dir> about:blank
// 3. node demo-deck/process-twin/capture.mjs
//
// Reads the process id from artifacts/results/ids.json and writes PNGs to
// screenshots/. Node 22+ (built-in fetch and WebSocket). Logs in through
// the API from the page itself, as the 'Demo Presenter' editor.
import { readFile, writeFile, mkdir } from 'node:fs/promises'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const WEB = process.env.WEB ?? 'http://localhost:3000'
const API = process.env.API ?? 'http://localhost:8000'
const ids = JSON.parse(await readFile(resolve(here, 'artifacts/results/ids.json'), 'utf8'))
const out = resolve(here, 'screenshots')
await mkdir(out, { recursive: true })

const pages = await (await fetch('http://127.0.0.1:9333/json')).json()
const ws = new WebSocket(pages.find(p => p.type === 'page').webSocketDebuggerUrl)
await new Promise((r, j) => { ws.onopen = r; ws.onerror = j })
let seq = 0
const waiting = new Map()
ws.onmessage = m => { const d = JSON.parse(m.data); if (d.id && waiting.has(d.id)) { waiting.get(d.id)(d); waiting.delete(d.id) } }
const send = (method, params = {}) => new Promise(r => { const id = ++seq; waiting.set(id, r); ws.send(JSON.stringify({ id, method, params })) })
const pause = ms => new Promise(r => setTimeout(r, ms))
const evaluate = async expr => {
  const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true })
  return r.result?.result?.value ?? r.result?.exceptionDetails?.exception?.description
}
const go = async (path, wait = 3500) => { await send('Page.navigate', { url: WEB + path }); await pause(wait) }
const click = async (text, sel = 'button,[role=tab],a,summary') => console.log(await evaluate(`(() => {
  const els = [...document.querySelectorAll(${JSON.stringify(sel)})].filter(e => e.offsetParent !== null && e.innerText.trim().toLowerCase().includes(${JSON.stringify(text.toLowerCase())}))
  els.sort((a, b) => a.innerText.length - b.innerText.length)
  if (!els[0]) return 'NOT FOUND: ${text}'; els[0].scrollIntoView({ block: 'center' }); els[0].click(); return 'clicked ' + els[0].innerText.trim().slice(0, 50) })()`))
// Document coordinates of a heading, so a section is captured wherever it is on the page.
const top = async text => evaluate(`(() => { const e=[...document.querySelectorAll('h1,h2,h3,figcaption')].find(e => e.innerText.toLowerCase().includes(${JSON.stringify(text.toLowerCase())})); return e ? Math.max(0, e.getBoundingClientRect().top + window.scrollY - 24) : 0 })()`)
const shot = async (name, at = null, height = 900) => {
  const y = at === null ? 0 : await top(at)
  const r = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true, clip: { x: 0, y, width: 1600, height, scale: 1 } })
  await writeFile(resolve(out, name + '.png'), Buffer.from(r.result.data, 'base64')); console.log('shot', name, y)
}

await send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 900, deviceScaleFactor: 1, mobile: false })
await go('/')
console.log(await evaluate(`fetch(${JSON.stringify(API)} + '/api/auth/login', {method:'POST', credentials:'include', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name:'Demo Presenter', role:'editor'})}).then(r => r.status)`))
const P = ids.process

await go(`/processes/${P}/diagram`, 5000)
await shot('01-process')

await go(`/processes/${P}/blueprint`, 5000)
await shot('02-blueprint')
await click('Agents', '[role=tab]'); await pause(1500)
await shot('03-agents')

await go(`/processes/${P}/process-twin`, 5000)
await shot('04-operating-model', 'How the process runs today')
await shot('05-runs', 'Run the twin', 500)
await shot('06-report-headline', 'less people time')
await shot('07-steps', 'Step by step')
await shot('08-segments', 'By kind of case')
await shot('09-routes', 'Routes cases took', 600)

await go(`/processes/${P}/blueprint`, 5000)
await click('Orchestration', '[role=tab]'); await pause(2500)
await shot('10-rehearsal')
ws.close()
