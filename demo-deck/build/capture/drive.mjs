// Drives the headless Edge on :9333 through a list of steps (JSON file arg).
import { readFile, writeFile } from 'node:fs/promises'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
const here = dirname(fileURLToPath(import.meta.url))
const steps = JSON.parse(await readFile(process.argv[2], 'utf8'))
const pages = await (await fetch('http://127.0.0.1:9333/json')).json()
const ws = new WebSocket(pages.find(p => p.type === 'page').webSocketDebuggerUrl)
await new Promise((r, j) => { ws.onopen = r; ws.onerror = j })
let seq = 0; const waiting = new Map()
ws.onmessage = m => { const d = JSON.parse(m.data); if (d.id && waiting.has(d.id)) { waiting.get(d.id)(d); waiting.delete(d.id) } }
const send = (method, params = {}) => new Promise(r => { const id = ++seq; waiting.set(id, r); ws.send(JSON.stringify({ id, method, params })) })
const pause = ms => new Promise(r => setTimeout(r, ms))
const evaluate = async expr => { const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }); return r.result?.result?.value ?? r.result?.exceptionDetails?.exception?.description }
await send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 900, deviceScaleFactor: 1, mobile: false })
const clickJs = (text, nth = 0, sel = 'button,[role=tab],a,summary,tr,li,[role=button]') => `(() => {
  const els = [...document.querySelectorAll(${JSON.stringify(sel)})].filter(e => e.offsetParent !== null && e.innerText.trim().toLowerCase().includes(${JSON.stringify(text.toLowerCase())}))
  els.sort((a, b) => a.innerText.length - b.innerText.length)
  const el = els[${nth}]; if (!el) return 'NOT FOUND: ' + ${JSON.stringify(text)}
  el.scrollIntoView({ block: 'center' }); el.click(); return 'clicked: ' + el.innerText.trim().slice(0, 60) })()`
for (const s of steps) {
  if (s.go) { await send('Page.navigate', { url: 'http://127.0.0.1:4310' + s.go }); await pause(s.wait ?? 2500); await evaluate(`document.documentElement.style.zoom='${process.env.ZOOM ?? 0.8}'`); await pause(300) }
  else if (s.click) { console.log(await evaluate(clickJs(s.click, s.nth ?? 0, s.sel))); await pause(s.wait ?? 1500) }
  else if (s.select) { console.log(await evaluate(`(() => { for (const sel of document.querySelectorAll('select')) { const o=[...sel.options].find(o => o.text.toLowerCase().includes(${JSON.stringify(s.select.toLowerCase())})); if (o && sel.offsetParent!==null) { const set=Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set; set.call(sel,o.value); sel.dispatchEvent(new Event('change',{bubbles:true})); return 'selected '+o.text } } return 'NO OPTION' })()`)); await pause(s.wait ?? 1500) }
  else if (s.fill) { console.log(await evaluate(`(() => { const e=[...document.querySelectorAll('input,textarea')].find(e => e.offsetParent!==null && (e.placeholder||'').includes(${JSON.stringify(s.fill)})); if(!e) return 'NO INPUT'; const set=Object.getOwnPropertyDescriptor(e.constructor.prototype,'value').set; set.call(e,${JSON.stringify(s.value)}); e.dispatchEvent(new Event('input',{bubbles:true})); return 'filled' })()`)); await pause(400) }
  else if (s.eval) { console.log(await evaluate(s.eval)); await pause(s.wait ?? 500) }
  else if (s.scroll !== undefined) { await evaluate(typeof s.scroll === 'number' ? `window.scrollTo(0, ${s.scroll})` : `(() => { const e=[...document.querySelectorAll('h1,h2,h3,h4,legend,strong')].find(e => e.innerText.toLowerCase().includes(${JSON.stringify(String(s.scroll).toLowerCase())})); if (e) e.scrollIntoView({block:'start'}); else console.log('no heading') })()`); await pause(s.wait ?? 600) }
  else if (s.probe) {
    console.log(await evaluate(`(() => { const t = s => [...document.querySelectorAll(s)].filter(e => e.offsetParent !== null).map(e => e.innerText.trim().split(String.fromCharCode(10)).join(' | ').slice(0,50)).filter(Boolean);
      return JSON.stringify({ url: location.pathname, height: document.documentElement.scrollHeight, headings: t('h1,h2,h3'), tabs: t('[role=tab]'), buttons: t('button').slice(0, 60), links: t('a').slice(0, 40) }, null, 1) })()`))
  }
  else if (s.shot) {
    const params = { format: 'png' }
    if (s.clip) { params.clip = { ...s.clip, scale: 1 } }
    if (s.full) { const h = await evaluate('document.documentElement.scrollHeight'); params.clip = { x: 0, y: 0, width: 1600, height: Math.min(h, s.maxHeight ?? 2400), scale: 1 }; params.captureBeyondViewport = true }
    const r = await send('Page.captureScreenshot', params)
    await writeFile(resolve(here, s.shot + '.png'), Buffer.from(r.result.data, 'base64')); console.log('shot', s.shot)
  }
}
ws.close()
