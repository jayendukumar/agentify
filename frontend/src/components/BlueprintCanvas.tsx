import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react'
import NavigatedViewer from 'bpmn-js/lib/NavigatedViewer'
import 'bpmn-js/dist/assets/diagram-js.css'
import 'bpmn-js/dist/assets/bpmn-font/css/bpmn-embedded.css'

export interface BlueprintCanvasHandle {
  zoomToFit(): void
  exportSvg(): Promise<string>
}

interface BlueprintCanvasProps {
  xml: string
  // node id -> CSS marker class, applied via canvas.addMarker for overlay coloring.
  markers: Record<string, string>
  onSelectionChange: (elementId: string | null) => void
  // Fired once per import with element id -> BPMN name, since the finalized
  // XML has no metadata join available (unlike the live process schema --
  // see ElementDetailPanel's comment on the same limitation).
  onDiagramReady: (labelsById: Record<string, string>) => void
}

const BlueprintCanvas = forwardRef<BlueprintCanvasHandle, BlueprintCanvasProps>(function BlueprintCanvas(
  { xml, markers, onSelectionChange, onDiagramReady },
  ref,
) {
  const containerRef = useRef<HTMLDivElement>(null)
  const viewerRef = useRef<NavigatedViewer | null>(null)
  const importedXmlRef = useRef<string | null>(null)
  const appliedMarkersRef = useRef<Map<string, string>>(new Map())
  const [diagramReady, setDiagramReady] = useState(false)

  useEffect(() => {
    if (!containerRef.current) return

    const viewer = new NavigatedViewer({ container: containerRef.current })
    viewerRef.current = viewer

    viewer.on('selection.changed', (event: { newSelection: Array<{ id: string }> }) => {
      const [selected] = event.newSelection
      onSelectionChange(selected ? selected.id : null)
    })

    return () => {
      viewer.destroy()
      viewerRef.current = null
      importedXmlRef.current = null
      appliedMarkersRef.current = new Map()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const viewer = viewerRef.current
    if (!viewer || !xml || xml === importedXmlRef.current) return

    setDiagramReady(false)
    viewer
      .importXML(xml)
      .then(() => {
        importedXmlRef.current = xml
        viewer.get<{ zoom(fit: string, center?: string): void }>('canvas').zoom('fit-viewport', 'auto')

        const elementRegistry =
          viewer.get<{ getAll(): Array<{ id: string; businessObject?: { name?: string } }> }>('elementRegistry')
        const labels: Record<string, string> = {}
        for (const el of elementRegistry.getAll()) {
          if (el.businessObject?.name) labels[el.id] = el.businessObject.name
        }
        onDiagramReady(labels)
        setDiagramReady(true)
      })
      .catch((err: Error) => {
        console.error('Failed to render BPMN diagram', err)
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [xml])

  useEffect(() => {
    if (!diagramReady) return
    const viewer = viewerRef.current
    if (!viewer) return
    const canvas =
      viewer.get<{ addMarker(id: string, cls: string): void; removeMarker(id: string, cls: string): void }>('canvas')

    for (const [id, cls] of appliedMarkersRef.current) {
      try {
        canvas.removeMarker(id, cls)
      } catch {
        // element no longer present -- ignore
      }
    }

    const next = new Map<string, string>()
    for (const [id, cls] of Object.entries(markers)) {
      try {
        canvas.addMarker(id, cls)
        next.set(id, cls)
      } catch {
        // unknown element id -- ignore
      }
    }
    appliedMarkersRef.current = next
  }, [markers, diagramReady])

  useImperativeHandle(ref, () => ({
    zoomToFit() {
      viewerRef.current?.get<{ zoom(fit: string, center?: string): void }>('canvas').zoom('fit-viewport', 'auto')
    },
    async exportSvg() {
      if (!viewerRef.current) return ''
      const { svg } = await viewerRef.current.saveSVG()
      return svg
    },
  }))

  return <div className="bpmn-canvas" ref={containerRef} />
})

export default BlueprintCanvas
