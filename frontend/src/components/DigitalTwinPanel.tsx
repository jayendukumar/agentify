import { useState } from 'react'
import type { AgentGroup } from '../lib/blueprintLabels'
import DigitalTwinAdvancedPanel from './DigitalTwinAdvancedPanel'
import TwinTestBuilder from './TwinTestBuilder'

// Epic 24, US24.7: the "Digital Twin Simulation" tab -- the guided,
// business-user test builder by default, with the original JSON-level
// scenario/environment/baseline tooling one click away under "Advanced".
type Mode = 'guided' | 'advanced'

export default function DigitalTwinPanel({ processId, groups }: { processId: string; groups: AgentGroup[] }) {
  const [mode, setMode] = useState<Mode>('guided')

  return (
    <div className="page" data-testid="digital-twin-panel">
      <div className="twin-mode-bar">
        <div>
          <h2 className="twin-mode-title">Test your agents before they go live</h2>
          <p className="meta">Try realistic situations in a simulated copy of the process. Nothing real is touched.</p>
        </div>
        <div className="twin-mode-toggle" role="group" aria-label="Setup mode">
          <button
            type="button"
            className={mode === 'guided' ? 'button-primary' : 'button-secondary'}
            aria-pressed={mode === 'guided'}
            onClick={() => setMode('guided')}
          >
            Guided
          </button>
          <button
            type="button"
            className={mode === 'advanced' ? 'button-primary' : 'button-secondary'}
            aria-pressed={mode === 'advanced'}
            onClick={() => setMode('advanced')}
          >
            Advanced
          </button>
        </div>
      </div>
      {mode === 'guided' ? (
        <TwinTestBuilder processId={processId} groups={groups} />
      ) : (
        <DigitalTwinAdvancedPanel processId={processId} groups={groups} />
      )}
    </div>
  )
}
