// Dependency-free browser smoke review. Run after `npm run build`.
// Uses isolated fixture responses; never reads or changes the user's API data.
import { createServer } from 'node:http'
import { readFile, writeFile, mkdir, mkdtemp } from 'node:fs/promises'
import { join, extname, resolve } from 'node:path'
import { tmpdir } from 'node:os'
import { spawn } from 'node:child_process'
import assert from 'node:assert/strict'

const output = resolve('.ui-review')
await mkdir(output, { recursive: true })
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.png': 'image/png', '.gif': 'image/gif', '.svg': 'image/svg+xml' }
const server = createServer(async (req, res) => {
  try {
    const path = decodeURIComponent(new URL(req.url, 'http://localhost').pathname)
    const file = extname(path) ? join(resolve('dist'), path) : resolve('dist/index.html')
    if (!file.startsWith(resolve('dist'))) { res.writeHead(403).end(); return }
    res.setHeader('Content-Type', mime[extname(file)] ?? 'application/octet-stream')
    res.end(await readFile(file))
  } catch { res.writeHead(404).end() }
})
await new Promise(r => server.listen(0, '127.0.0.1', r))
const origin = `http://127.0.0.1:${server.address().port}`
const profile = await mkdtemp(join(tmpdir(), 'axyntro-ui-review-'))
const browser = spawn(process.env.CHROME_PATH ?? 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', [
  '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
  '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank',
], { windowsHide: true, stdio: 'ignore' })
const pause = ms => new Promise(r => setTimeout(r, ms))
let ws
try {
  let port
  for (let i = 0; i < 100; i++) {
    try { port = (await readFile(join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]; break } catch { await pause(100) }
  }
  assert(port, 'Headless browser did not start')
  const pages = await (await fetch(`http://127.0.0.1:${port}/json`)).json()
  ws = new WebSocket(pages.find(p => p.type === 'page').webSocketDebuggerUrl)
  await new Promise((r, reject) => { ws.onopen = r; ws.onerror = reject })
  let sequence = 0
  const pending = new Map()
  const exceptions = []
  ws.onmessage = event => {
    const message = JSON.parse(event.data)
    if (message.id) {
      const task = pending.get(message.id)
      pending.delete(message.id)
      if (message.error) task.reject(message.error); else task.resolve(message.result)
    }
    if (message.method === 'Runtime.exceptionThrown') exceptions.push(message.params.exceptionDetails.text)
  }
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++sequence
    pending.set(id, { resolve, reject })
    ws.send(JSON.stringify({ id, method, params }))
  })
  const evaluate = async expression => {
    const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text)
    return result.result.value
  }
  const waitFor = async expression => {
    for (let i = 0; i < 100; i++) { if (await evaluate(expression)) return; await pause(100) }
    throw new Error(`Timed out: ${expression}`)
  }
  await send('Page.enable')
  await send('Runtime.enable')
  await send('Page.addScriptToEvaluateOnNewDocument', { source: `(${fixtures.toString()})()` })
  const report = []
  const contrastFailures = []
  const screens = [
    ['home', '/', 'h1'],
    ['process', '/processes/proc-1', '.stepper'],
    ['diagram', '/processes/proc-1/diagram', '.djs-container'],
    ['blueprint', '/processes/proc-1/blueprint', '[role=tablist]'],
    ['registries', '/registries', '.registry-entry-card'],
    ['versions', '/processes/proc-1/versions', '.version-row'],
    ['gaps', '/processes/proc-1/gaps', '.gap-finding-card'],
  ]
  for (const width of [768, 1024, 1440]) {
    await send('Emulation.setDeviceMetricsOverride', { width, height: 1000, deviceScaleFactor: 1, mobile: false })
    for (const [name, route, ready] of screens) {
      await send('Page.navigate', { url: origin + route })
      await waitFor(`document.querySelector(${JSON.stringify(ready)}) !== null`)
      await pause(150)
      const layout = await evaluate(`({ width: innerWidth, content: document.documentElement.scrollWidth, heading: document.querySelector('h1')?.textContent, unlabeled: [...document.querySelectorAll('input:not([hidden]), select, textarea')].filter(el => !el.closest('label') && !el.labels?.length && !el.getAttribute('aria-label') && !el.getAttribute('aria-labelledby')).length })`)
      assert(layout.content <= width, `${name} overflows at ${width}: ${layout.content}`)
      assert.equal(layout.unlabeled, 0, `${name} has unlabeled controls`)
      report.push({ name, ...layout })
      contrastFailures.push(...(await evaluate(`(${auditContrast.toString()})()`)).map(issue => ({ screen: name, width, ...issue })))
      const shot = await send('Page.captureScreenshot', { format: 'png' })
      await writeFile(join(output, `${name}-${width}.png`), Buffer.from(shot.data, 'base64'))
      if (name === 'blueprint') {
        await evaluate(`document.querySelector('#tab-blueprint').focus()`)
        await send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'ArrowRight', code: 'ArrowRight' })
        await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'ArrowRight', code: 'ArrowRight' })
        await waitFor(`document.querySelector('#tab-agents').getAttribute('aria-selected') === 'true'`)
        assert.equal(await evaluate(`document.activeElement.id`), 'tab-agents')
        assert.equal(await evaluate(`document.documentElement.scrollWidth <= innerWidth`), true)
        contrastFailures.push(...(await evaluate(`(${auditContrast.toString()})()`)).map(issue => ({ screen: 'agents', width, ...issue })))
        const agentShot = await send('Page.captureScreenshot', { format: 'png' })
        await writeFile(join(output, `agents-${width}.png`), Buffer.from(agentShot.data, 'base64'))
      }
    }
    await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] })
    await send('Page.navigate', { url: origin + '/?review=login' })
    await waitFor(`document.querySelector('.login-intro') !== null`)
    assert.equal(await evaluate(`document.querySelector('.login-intro img').src.endsWith('.gif')`), false)
    await evaluate(`document.querySelector('.login-intro-skip').click()`)
    await waitFor(`document.querySelector('.login-form') !== null`)
    assert.equal(await evaluate(`document.documentElement.scrollWidth <= innerWidth`), true)
    contrastFailures.push(...(await evaluate(`(${auditContrast.toString()})()`)).map(issue => ({ screen: 'login', width, ...issue })))
    const shot = await send('Page.captureScreenshot', { format: 'png' })
    await writeFile(join(output, `login-${width}.png`), Buffer.from(shot.data, 'base64'))
    await send('Emulation.setEmulatedMedia', { features: [] })
  }
  assert.deepEqual(exceptions, [], 'Unexpected browser exceptions')
  await writeFile(join(output, 'report.json'), JSON.stringify({ screens: report, keyboardTabs: 'passed', reducedMotion: 'passed', contrastFailures, exceptions }, null, 2))
  assert.deepEqual(contrastFailures, [], 'Rendered text contrast failures; see .ui-review/report.json')
  console.log(`Passed ${report.length} screen/viewport checks, blueprint keyboard tabs and reduced-motion login. Screenshots: ${output}`)
} finally {
  ws?.close()
  browser.kill()
  server.close()
}

function auditContrast() {
  const rgb = value => value.match(/[\d.]+/g)?.map(Number) ?? [0, 0, 0, 0]
  const luminance = color => color.slice(0, 3).map(v => v / 255).map(v => v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4).reduce((sum, v, i) => sum + v * [0.2126, 0.7152, 0.0722][i], 0)
  const backgrounds = el => {
    if (!el) return [[255, 255, 255]]
    const style = getComputedStyle(el)
    const gradient = style.backgroundImage.match(/rgba?\([^)]+\)/g)
    if (gradient?.length) return gradient.map(rgb)
    const color = rgb(style.backgroundColor)
    if (color.length === 3 || color[3] === 1) return [color]
    return backgrounds(el.parentElement)
  }
  return [...document.querySelectorAll('body *')].filter(el =>
    el instanceof HTMLElement && el.checkVisibility() && !el.closest(':disabled, .sr-only, .skip-link') &&
    [...el.childNodes].some(n => n.nodeType === Node.TEXT_NODE && n.textContent.trim()),
  ).flatMap(el => {
    const style = getComputedStyle(el)
    const fg = luminance(rgb(style.color))
    const ratio = Math.min(...backgrounds(el).map(bg => {
      const value = luminance(bg)
      return (Math.max(fg, value) + 0.05) / (Math.min(fg, value) + 0.05)
    }))
    const large = parseFloat(style.fontSize) >= 24 || (parseFloat(style.fontSize) >= 18.666 && parseFloat(style.fontWeight) >= 700)
    return ratio + 0.01 < (large ? 3 : 4.5) ? [{ text: el.textContent.trim().slice(0, 90), foreground: style.color, ratio: +ratio.toFixed(2), required: large ? 3 : 4.5 }] : []
  })
}

function fixtures() {
  const now = '2026-09-22T08:00:00Z'
  const process = { id: 'proc-1', name: 'Supplier invoice approval', document_count: 2, has_draft_bpmn: true, finalized_version_count: 2, created_at: now, updated_at: now, process_schema: null }
  const xml = '<?xml version="1.0"?><bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" xmlns:di="http://www.omg.org/spec/DD/20100524/DI" id="Definitions_1" targetNamespace="http://bpmn.io/schema/bpmn"><bpmn:process id="Process_1"><bpmn:startEvent id="Start_1" name="Invoice received"/><bpmn:task id="Task_a" name="Review invoice"/><bpmn:endEvent id="End_1" name="Approved"/><bpmn:sequenceFlow id="Flow_1" sourceRef="Start_1" targetRef="Task_a"/><bpmn:sequenceFlow id="Flow_2" sourceRef="Task_a" targetRef="End_1"/></bpmn:process><bpmndi:BPMNDiagram id="Diagram_1"><bpmndi:BPMNPlane id="Plane_1" bpmnElement="Process_1"><bpmndi:BPMNShape id="S1" bpmnElement="Start_1"><dc:Bounds x="100" y="120" width="36" height="36"/></bpmndi:BPMNShape><bpmndi:BPMNShape id="S2" bpmnElement="Task_a"><dc:Bounds x="200" y="98" width="100" height="80"/></bpmndi:BPMNShape><bpmndi:BPMNShape id="S3" bpmnElement="End_1"><dc:Bounds x="365" y="120" width="36" height="36"/></bpmndi:BPMNShape><bpmndi:BPMNEdge id="E1" bpmnElement="Flow_1"><di:waypoint x="136" y="138"/><di:waypoint x="200" y="138"/></bpmndi:BPMNEdge><bpmndi:BPMNEdge id="E2" bpmnElement="Flow_2"><di:waypoint x="300" y="138"/><di:waypoint x="365" y="138"/></bpmndi:BPMNEdge></bpmndi:BPMNPlane></bpmndi:BPMNDiagram></bpmn:definitions>'
  const overlay = { process_id: 'proc-1', baseline_version_id: 'ver-1', generated_at: now, nodes: [{ node_id: 'Task_a', verdict: 'automatable', step_type: 'rule_based_decision', rationale: 'Match invoice details against purchase order rules.', overridden: false, agent_spec: { name: 'Invoice review agent', purpose: 'Validate supplier invoices against purchase orders and flag exceptions for human review.', trigger: 'Invoice received', required_inputs: [], expected_outputs: [], tools_systems_needed: ['Finance system'], human_checkpoint: 'review_before_action', consolidated_from_nodes: [] } }] }
  const originalFetch = window.fetch.bind(window)
  window.fetch = async (input, init) => {
    const url = new URL(typeof input === 'string' ? input : input.url, location.origin)
    if (!url.pathname.startsWith('/api/')) return originalFetch(input, init)
    const path = url.pathname
    let data = []
    let status = 200
    if (path === '/api/auth/me') {
      data = { id: 'user-1', name: 'Alex Morgan', role: 'editor', created_at: now }
      if (location.search.includes('review=login')) { status = 401; data = { detail: 'Not logged in' } }
    } else if (path === '/api/processes') data = [process, { ...process, id: 'proc-2', name: 'Customer onboarding', document_count: 1, finalized_version_count: 0 }, { ...process, id: 'proc-3', name: 'Employee expense reimbursement', has_draft_bpmn: false, finalized_version_count: 0 }]
    else if (path === '/api/processes/proc-1') data = process
    else if (path.endsWith('/documents')) data = [{ id: 'doc-1', filename: 'Supplier invoice approval procedure.pdf', status: 'done', size_bytes: 42000 }, { id: 'doc-2', filename: 'Finance approval matrix.docx', status: 'processing', size_bytes: 16000 }]
    else if (path.endsWith('/bpmn')) data = { process_id: 'proc-1', xml, low_confidence_element_ids: [], validation_issues: [], generated_at: now }
    else if (path.endsWith('/blueprint')) data = overlay
    else if (path.endsWith('/versions')) data = [{ id: 'ver-1', process_id: 'proc-1', label: 'Reviewed baseline', created_at: now, created_by_name: 'Alex Morgan' }, { id: 'ver-2', process_id: 'proc-1', label: 'Initial review', created_at: now }]
    else if (path.endsWith('/versions/ver-1')) data = { id: 'ver-1', xml }
    else if (path.includes('gap-findings')) data = [{ id: 'gap-1', kind: 'missing_detail', question: 'Who approves invoices above the standard spending limit?', status: 'open', options: [{ id: 'opt-1', label: 'Finance manager' }], target_element_ids: ['Task_a'] }]
    else if (path === '/api/registries') data = [{ name: 'local', type: 'local', reachable: true, authenticated: true }]
    else if (path === '/api/registries/search') data = { entries: [{ id: 'entry-1', registry_name: 'local', agent_name: 'Invoice review agent', tags: ['Finance', 'Review'], pushed_at: now, pushed_by_name: 'Alex Morgan', definition: { name: 'Invoice review agent', purpose: 'Validate invoices' } }], registry_errors: {} }
    return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } })
  }
}
