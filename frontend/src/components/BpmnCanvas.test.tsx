import { createRef } from 'react'
import { render, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import BpmnCanvas, { type BpmnCanvasHandle } from './BpmnCanvas'

const handlers: Record<string, (event: unknown) => void> = {}
const commandStack = { undo: vi.fn(), redo: vi.fn() }
const canvasService = { zoom: vi.fn() }
const importXML = vi.fn().mockResolvedValue({ warnings: [] })
const saveXML = vi.fn().mockResolvedValue({ xml: '<exported/>' })
const saveSVG = vi.fn().mockResolvedValue({ svg: '<svg exported/>' })
const destroy = vi.fn()

// bpmn-js relies on real SVG layout (getBBox, ResizeObserver) that jsdom
// doesn't implement, so the underlying Modeler is mocked here -- this test
// verifies BpmnCanvas's wiring contract (props -> Modeler calls, Modeler
// events -> prop callbacks, ref -> Modeler methods), not bpmn-js itself.
vi.mock('bpmn-js/lib/Modeler', () => ({
  default: class MockModeler {
    on(event: string, callback: (e: unknown) => void) {
      handlers[event] = callback
    }
    get(name: string) {
      if (name === 'commandStack') return commandStack
      if (name === 'canvas') return canvasService
      throw new Error(`unexpected service ${name}`)
    }
    importXML = importXML
    saveXML = saveXML
    saveSVG = saveSVG
    destroy = destroy
  },
}))

describe('BpmnCanvas', () => {
  it('imports the given xml and zooms to fit', async () => {
    render(<BpmnCanvas xml="<xml/>" onSelectionChange={() => {}} onDirtyChange={() => {}} />)
    await waitFor(() => expect(importXML).toHaveBeenCalledWith('<xml/>'))
    expect(canvasService.zoom).toHaveBeenCalledWith('fit-viewport')
  })

  it('reports selection changes via onSelectionChange', async () => {
    const onSelectionChange = vi.fn()
    render(<BpmnCanvas xml="<xml/>" onSelectionChange={onSelectionChange} onDirtyChange={() => {}} />)
    await waitFor(() => expect(importXML).toHaveBeenCalled())

    handlers['selection.changed']({ newSelection: [{ id: 'Task_el-1' }] })
    expect(onSelectionChange).toHaveBeenCalledWith('Task_el-1')

    handlers['selection.changed']({ newSelection: [] })
    expect(onSelectionChange).toHaveBeenCalledWith(null)
  })

  it('reports dirty on commandStack.changed', async () => {
    const onDirtyChange = vi.fn()
    render(<BpmnCanvas xml="<xml/>" onSelectionChange={() => {}} onDirtyChange={onDirtyChange} />)
    await waitFor(() => expect(importXML).toHaveBeenCalled())

    handlers['commandStack.changed']({})
    expect(onDirtyChange).toHaveBeenCalledWith(true)
  })

  it('exposes undo/redo/export via the imperative handle', async () => {
    const ref = createRef<BpmnCanvasHandle>()
    render(<BpmnCanvas ref={ref} xml="<xml/>" onSelectionChange={() => {}} onDirtyChange={() => {}} />)
    await waitFor(() => expect(importXML).toHaveBeenCalled())

    ref.current?.undo()
    expect(commandStack.undo).toHaveBeenCalled()

    ref.current?.redo()
    expect(commandStack.redo).toHaveBeenCalled()

    await expect(ref.current?.exportXml()).resolves.toBe('<exported/>')
    await expect(ref.current?.exportSvg()).resolves.toBe('<svg exported/>')
  })
})
