# Agent Federation Process

This directory defines role personas used to coordinate AI-assisted development.

See `agents/langgraph-usage-note.md` for when it makes sense to use the repo's LangGraph workflow versus a direct implementation request.

## Recommended Structure

- One role spec per file (already set up).
- One shared process contract (this file).
- One durable knowledge base under `agents/knowledge/` for research write-ups, verified system notes, and investigation handoffs.
- One dedicated runbook directory under `agents/runbooks/` for reusable operational and implementation guides.
- One set of shared templates under `agents/templates/` (optional next step).

## Current Roles

- Project Coordinator
- Project Manager
- Architect
- UX
- Designer
- Engineer (Web Dev)
- QA
- Safety

## Suggested Execution Loop

1. **Intake (Coordinator + PM)**
   - Convert request into a work packet.
2. **Solution Framing (Architect + UX + Designer)**
   - Align technical approach and user experience before implementation.
3. **Build (Engineering)**
   - Implement in small vertical slices.
4. **Validation (QA + Safety)**
   - Verify acceptance criteria and risk posture.
5. **Release Decision (PM + Coordinator)**
   - Approve, defer, or rollback with explicit rationale.

## Work Packet Template (minimum)

- Problem statement
- Scope / non-goals
- Acceptance criteria
- Constraints (tech, time, policy)
- Dependencies
- Owner + due date

## Handoff Contract (between any two agents)

- What changed
- Why this approach
- Open risks
- Exact next action expected from receiver
- Blocking questions

## Better-Than-Basic Process (recommended)

Instead of only role markdown files, add these lightweight controls:

- **Decision Log**: `agents/decision-log.md` for architecture/product tradeoffs.
- **Risk Register**: `agents/risk-register.md` with owner + mitigation due date.
- **Definition of Ready / Done** checklists used by PM, QA, Safety.
- **Release Gate** checklist requiring QA + Safety signoff for high-risk changes.

## Operating Principles

- Prefer small, testable increments.
- Make assumptions explicit.
- Keep a single source of truth for status and decisions.
- Escalate blockers quickly; do not silently workaround requirement gaps.

## Knowledge Base

- Store reusable findings under `agents/knowledge/` when they would save future investigation time.
- Prefer one topic per file with a clear title, verification date, evidence summary, current conclusion, and next checks.
- Use this for upstream API behavior, environment quirks, architectural decisions, and repeated debugging outcomes.

## Runbooks

- Store reusable execution guides under `agents/runbooks/`.
- Keep runbooks task-oriented: include context, exact commands, validation steps, and rollback notes when applicable.
