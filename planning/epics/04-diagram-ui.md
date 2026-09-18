# Epic 4 -- Process Diagram UI

Goal: Present the generated BPMN diagram in an interactive canvas as the
baseline artifact users work with, with direct-manipulation editing as a
fallback to chat.

Depends on: Epic 3 (BPMN XML to render), Epic 9 (frontend scaffolding).
Feeds: Epic 5 (chat sits alongside this), Epic 6 (finalization).

## User Stories

US4.1 -- Interactive BPMN canvas.
As a Process Analyst, I want to view the generated BPMN diagram in a
pan/zoom/select canvas (e.g. using bpmn-js or an equivalent BPMN 2.0
renderer), so that I can inspect the as-is process visually.

US4.2 -- Manual direct-manipulation editing.
As a Process Analyst, I want to drag, resize, and connect diagram elements
directly on the canvas, so that I have a fallback for edits that are easier
to do by hand than to describe in chat.

US4.3 -- Node/edge detail panel.
As a Process Analyst, I want to click a node or connection and see its
attached metadata (description, actor, source document excerpt), so that I
can understand what the system based this element on.

US4.4 -- Diagram export.
As a Process Analyst, I want to export the diagram as PNG/SVG or download
the raw BPMN 2.0 XML, so that I can share it outside the tool or open it in
another BPMN tool.

US4.5 -- Multi-process selection.
As a Process Analyst, I want to switch between multiple processes/diagrams
I have created, so that I can manage more than one process in the same
workspace.

US4.6 -- Undo/redo history.
As a Process Analyst, I want to undo/redo diagram edits (whether made
manually or via chat), so that I can experiment without fear of losing the
previous state.

## Notes / Open Questions

Direct-manipulation edits (US4.2) and chat-driven edits (Epic 5) must write
to the same underlying diagram model so the two stay in sync -- this is a
shared-state design concern to resolve early, not per-story.
