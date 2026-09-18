# Epic 5 -- Conversational Diagram Editing

Goal: Let users add, amend, or delete diagram nodes and relationships (and
ask questions about the diagram) through a chat interface tied to the
current process diagram.

Depends on: Epic 3 (diagram model), Epic 4 (diagram UI it sits alongside).
Feeds: Epic 6 (finalization happens once chat-driven review is done).

## User Stories

US5.1 -- Chat panel tied to the current diagram.
As a Process Analyst, I want a chat panel next to the diagram that has
context of the currently loaded process, so that I can refer to "this step"
or "the approval task" and have it understood.

US5.2 -- Natural-language diagram edit intents.
As a Process Analyst, I want to type instructions like "add a review step
after the approval task", "remove the manual data entry step", or "rename
this task", so that I can edit the diagram without manually manipulating
shapes.

US5.3 -- Diff preview before applying a chat edit.
As a Process Analyst, I want to see a preview of what a chat instruction
will change on the diagram, and confirm or reject it, so that the system
never silently mutates my diagram based on a misread instruction.

US5.4 -- Clarifying questions on ambiguous instructions.
As a Process Analyst, I want the chat to ask a clarifying question when my
instruction is ambiguous (e.g., which of two similarly named steps I mean),
so that edits are applied correctly rather than guessed.

US5.5 -- Chat/edit history and audit trail.
As a Process Analyst, I want a history of chat-driven changes with what
changed and when, so that I (and reviewers) can audit how the diagram
evolved.

US5.6 -- Ask the chat to explain the diagram.
As a Process Analyst, I want to ask the chat questions about the existing
diagram ("what does this step do", "who owns this task"), so that I can use
the same interface to understand the process, not just edit it.

## Notes / Open Questions

See the bpmn-chat-ops skill for the intent taxonomy, diff/patch schema, and
confirm-before-apply pattern this epic should implement.

US5.3's diff preview is a hard product requirement, not optional polish --
unconfirmed chat edits silently changing a process diagram is a correctness
and trust risk.
