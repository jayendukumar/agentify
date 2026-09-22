# Axyntro interface improvement

## Audit

The application uses React 19, TypeScript, Vite, bpmn-js, and a shared CSS
stylesheet. Existing brand CSS/JSON and brand standards are available;
`AXYNTRO_PROJECT_CONTEXT.md` was not found. No backend or API changes are needed.

Priority issues observed in the implementation:
- Diagram views escape the content container with 100vw and combine two fixed
  320px sidebars without responsive breakpoints.
- Global primary-button styling competes with tabs and agent cards, including
  white card titles on white surfaces. Most toolbars have no action hierarchy.
- Form controls have inconsistent sizes, weak boundaries, and missing labels.
  The hidden document input's label cannot be operated from the keyboard.
- Navigation lacks a current-page indicator and skip link. Blueprint tabs lack
  arrow-key navigation and associated panels.
- Loading/error/empty states are inconsistent; create-process has no pending
  guard, retry feedback, or confirmation of successful creation.
- The 10-second GIF intro cannot be paused and ignores reduced-motion settings.
- Layout refresh replaces manual work without confirmation. Scenario deletion
  and baseline clearing need confirmation.
- A light/dark browser color-scheme declaration conflicts with the light brand.

## Implementation stages

1. Extend existing brand tokens with accessible semantic aliases, typography,
   control dimensions and layout sizes. Improve shared styles and responsive
   canvas/sidebar layouts without replacing components or touching BPMN internals.
2. Improve navigation, page headings, login, process creation/upload, registry
   search, action hierarchy and live feedback. Preserve role rules and routes.
3. Add keyboard tab behavior, destructive-action confirmation and reduced-motion
   handling; keep the requested 10-second intro for standard-motion users.
4. Run lint, existing frontend tests and build; add targeted regression checks
   for newly fixed interaction defects. Inspect representative views at 768px,
   1024px and 1440px using a local browser, with fixture API responses where
   necessary to avoid altering user data.

## Boundaries

Preserve existing backend/login-role fixes and the uncommitted digital-twin
preview implementation. No new UI framework, API, route, database, or workflow.
Reuse the supplied logos, semantic status colors and light hero gradient.
Validation results and remaining recommendations will be recorded here.

## Completed improvements

- Bright shared surfaces, consistent headings, stronger form boundaries, 44px
  controls, accessible link colors, and clear primary/secondary action styling.
- Current-section navigation, a skip link, descriptive page headings, labeled
  forms, live errors/statuses, keyboard upload and keyboard blueprint inspection.
- Responsive canvas layouts: side panels move below the canvas on tablets.
  Centered diagrams and a Fit diagram action prevent palette overlap.
- Process creation has a pending guard, success feedback and recoverable errors.
  Upload failures preserve the loaded process instead of replacing the screen.
- Blueprint and simulation tabs support arrow keys, Home/End, roving focus,
  selected-state semantics and associated panels. Agent cards expose selection.
- Native confirmations protect layout replacement, blueprint regeneration,
  scenario deletion and baseline clearing. Existing version-restore confirmation
  remains. Deletion failures now surface instead of becoming unhandled promises.
- The requested 10-second introduction remains; reduced-motion users see the
  approved static logo, and everyone can skip the introduction.
- Diagram/blueprint modules are loaded on demand. The initial JS entry decreased
  from 925.58 kB (271.26 kB gzip) to 296.69 kB (91.40 kB gzip), approximately 68%
  smaller uncompressed. The final build has no oversized-chunk warning.

## Validation results

- Baseline: 72 frontend tests passed; lint returned 14 existing warnings.
- Final: `npm test` — **78 tests passed across 14 files**.
- `npm run lint` — exit 0, same **14 existing warnings**, no new warnings.
- `npm run build` — TypeScript and Vite production build passed.
- `git diff --check` — passed (Git also emits local LF/CRLF notices).
- `node scripts/ui-review.mjs` — 21 main screen/viewport checks at **768, 1024
  and 1440px**, plus Agents and Login at each width. No document overflow,
  unlabeled visible form controls or uncaught browser exceptions in those views.
- Real headless Edge rendered Home, Process detail, Diagram, Blueprint, Agents,
  Registries, Versions, Gap review and Login, using isolated fixture responses.
  Blueprint keyboard tab activation and reduced-motion login passed.
- Rendered text contrast checks passed on sampled views, using 4.5:1 for normal
  text and 3:1 for large text. One 4.33:1 hero-caption failure was found and fixed
  using Slate. Agent Blue primary labels are 19px/700 to meet large-text contrast.
- Visually inspected desktop Home/Agents and tablet Diagram/Login screenshots;
  confirmed the diagram-centering correction in a subsequent tablet screenshot.
- Screenshots and machine-readable results are in `frontend/.ui-review/`
  (ignored by Git). The browser review script is retained for repeatable checks.

These checks are not a complete WCAG certification or live-backend acceptance
test. Backend files, API contracts, routes and database behavior were not changed
in this UI pass. Pre-existing backend role fixes and digital-twin preview changes
were retained.

## Changed files in this UI pass

| Area | Files |
| --- | --- |
| Shell and shared style | `frontend/src/App.tsx`, `frontend/src/main.tsx`, `frontend/src/index.css`, `frontend/src/styles/axyntro-tokens.css`, `frontend/src/styles/interface.css` |
| Login | `frontend/src/auth/LoginPage.tsx`, `frontend/src/auth/LoginPage.css`, `frontend/src/auth/LoginPage.test.tsx` |
| Shared components | `frontend/src/components/AgentCardsPanel.tsx`, `BlueprintCanvas.tsx`, `BpmnCanvas.tsx`, `BpmnCanvas.test.tsx`, `ChatPanel.tsx`, `DigitalTwinPanel.tsx`, `ErrorBoundary.tsx`, `ProcessStepper.tsx` (all in the same components directory) |
| Screens | `frontend/src/pages/BlueprintPage.tsx`, `DiagramPage.tsx`, `DiagramPage.test.tsx`, `GapReviewPage.tsx`, `ProcessDetailPage.tsx`, `ProcessListPage.tsx`, `ProcessListPage.test.tsx`, `RegistriesPage.tsx`, `VersionsPage.tsx` (all in the same pages directory) |
| Keyboard behavior | `frontend/src/lib/tabKeyboard.ts` |
| Review tooling and notes | `frontend/scripts/ui-review.mjs`, `.gitignore`, `planning/ui-improvement-plan.md` |

## Remaining recommendations

- Conduct a manual screen-reader and keyboard review of bpmn-js's SVG editing
  palette and complex diagrams; the blueprint step selector supplies a keyboard
  path to assessments, but it does not replace every graphical editing action.
- Resolve the 14 pre-existing lint warnings in a focused follow-up (mostly effect
  state updates/dependencies, plus a test mock and Fast Refresh export warning).
- Exercise exceptionally large real documents, long multilingual labels and
  live provider failures in acceptance testing. Browser review here deliberately
  used fixtures to avoid altering user data or making paid LLM calls.
