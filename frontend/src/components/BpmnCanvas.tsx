import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react'
import BpmnModeler from 'bpmn-js/lib/Modeler'
import 'bpmn-js/dist/assets/diagram-js.css'
import 'bpmn-js/dist/assets/bpmn-font/css/bpmn-embedded.css'

export interface BpmnCanvasHandle {
  undo(): void
  redo(): void
  canUndo(): boolean
  canRedo(): boolean
  zoomToFit(): void
  exportXml(): Promise<string>
  exportSvg(): Promise<string>
}

interface BpmnCanvasProps {
  xml: string
  onSelectionChange: (elementId: string | null) => void
  onDirtyChange: (isDirty: boolean) => void
}

const BpmnCanvas = forwardRef<BpmnCanvasHandle, BpmnCanvasProps>(function BpmnCanvas(
  { xml, onSelectionChange, onDirtyChange },
  ref,
) {
  const containerRef = useRef<HTMLDivElement>(null)
  const modelerRef = useRef<BpmnModeler | null>(null)
  const importedXmlRef = useRef<string | null>(null)

  useEffect(() => {
    if (!containerRef.current) return

    const modeler = new BpmnModeler({ container: containerRef.current })
    modelerRef.current = modeler

    modeler.on('selection.changed', (event: { newSelection: Array<{ id: string }> }) => {
      const [selected] = event.newSelection
      onSelectionChange(selected ? selected.id : null)
    })

    // Any command stack change post-import is an unsaved edit. This
    // intentionally doesn't track canUndo() as "dirty" -- after a Save, the
    // stack still has undo-able entries even though there's nothing new to
    // persist, so the parent (DiagramPage) owns resetting dirty=false on a
    // successful save; this callback only ever announces "something changed".
    modeler.on('commandStack.changed', () => {
      onDirtyChange(true)
    })

    return () => {
      modeler.destroy()
      modelerRef.current = null
      importedXmlRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const modeler = modelerRef.current
    if (!modeler || !xml || xml === importedXmlRef.current) return

    modeler
      .importXML(xml)
      .then(() => {
        importedXmlRef.current = xml
        modeler.get<{ zoom(fit: string, center?: string): void }>('canvas').zoom('fit-viewport', 'auto')
        onDirtyChange(false)
      })
      .catch((err: Error) => {
        console.error('Failed to render BPMN diagram', err)
      })
  }, [xml, onDirtyChange])

  useImperativeHandle(ref, () => ({
    undo() {
      modelerRef.current?.get<{ undo(): void }>('commandStack').undo()
    },
    redo() {
      modelerRef.current?.get<{ redo(): void }>('commandStack').redo()
    },
    canUndo() {
      return modelerRef.current?.get<{ canUndo(): boolean }>('commandStack').canUndo() ?? false
    },
    canRedo() {
      return modelerRef.current?.get<{ canRedo(): boolean }>('commandStack').canRedo() ?? false
    },
    zoomToFit() {
      modelerRef.current?.get<{ zoom(fit: string, center?: string): void }>('canvas').zoom('fit-viewport', 'auto')
    },
    async exportXml() {
      if (!modelerRef.current) return ''
      const { xml: exported } = await modelerRef.current.saveXML({ format: true })
      return exported ?? ''
    },
    async exportSvg() {
      if (!modelerRef.current) return ''
      const { svg } = await modelerRef.current.saveSVG()
      return svg
    },
  }))

  return <div className="bpmn-canvas" ref={containerRef} />
})

export default BpmnCanvas
