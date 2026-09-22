export type StepStatus = 'done' | 'current' | 'upcoming'

export interface StepperStep {
  label: string
  description: string
  status: StepStatus
}

export default function ProcessStepper({ steps }: { steps: StepperStep[] }) {
  return (
    <ol className="stepper">
      {steps.map((step, index) => (
        <li key={step.label} className={`stepper-step stepper-step-${step.status}`} aria-current={step.status === 'current' ? 'step' : undefined}>
          <span className="stepper-marker" aria-hidden="true">
            {step.status === 'done' ? '✓' : index + 1}
          </span>
          <span className="stepper-body">
            <span className="sr-only">{step.status === 'done' ? 'Completed: ' : step.status === 'current' ? 'Current step: ' : 'Upcoming: '}</span>
            <span className="stepper-label">{step.label}</span>
            <span className="stepper-description">{step.description}</span>
          </span>
        </li>
      ))}
    </ol>
  )
}
