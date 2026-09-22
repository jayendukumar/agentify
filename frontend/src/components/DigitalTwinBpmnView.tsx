import { useMemo } from 'react'
import BlueprintCanvas from './BlueprintCanvas'
import { buildDigitalTwinBpmnXml } from '../lib/digitalTwinBpmn'
import type { ChainItem } from '../lib/digitalTwinChain'

const NO_MARKERS = {}
function noop() {
  // BlueprintCanvas requires selection/ready callbacks -- this view is
  // read-only and has no detail panel to feed.
}

export default function DigitalTwinBpmnView({
  chain,
  labelsById,
}: {
  chain: ChainItem[]
  labelsById: Record<string, string>
}) {
  const xml = useMemo(() => buildDigitalTwinBpmnXml(chain, labelsById), [chain, labelsById])

  if (!xml) {
    return <p className="meta">No steps evaluated yet -- generate a blueprint first.</p>
  }

  return (
    <div data-testid="digital-twin-bpmn-view">
      <p className="meta">
        Pools: Human, Agents, Systems &amp; applications. Solid arrows are step-to-step handoffs within a pool;
        dashed arrows are cross-pool connections -- an agent handing off to a human, an agent calling a system, or an
        agent escalating to a governance checkpoint.
      </p>
      <div className="twin-bpmn-canvas">
        <BlueprintCanvas xml={xml} markers={NO_MARKERS} onSelectionChange={noop} onDiagramReady={noop} />
      </div>
    </div>
  )
}
