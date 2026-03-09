# Runbook: Multi-Agent Codebase Improvement

You are using the term **runbook** correctly. In this context, it is an operational, repeatable procedure for executing planned engineering improvements.

## Purpose

Provide a repeatable workflow for coordinator-led, multi-agent code quality passes.

## Preconditions

- Local stack boots via Docker.
- Current branch is clean enough to isolate the tranche.
- Acceptance criteria are documented in a plan.

## Steps

1. **Coordinator Kickoff**
   - Gather role analyses from PM, Architect, UX, Designer, Engineer, QA, Safety.
   - Store outputs under `agents/reviews/`.
2. **Synthesis**
   - Build a single prioritized plan (`agents/plan-of-action.md`).
   - Identify low-risk/high-impact tranche for immediate execution.
3. **Execution**
   - Implement only the selected tranche.
   - Keep changes minimal and scoped.
4. **Validation**
   - Run static checks on touched files.
   - Run existing automated tests relevant to touched areas.
5. **Handoff**
   - Document completed items, residual risks, and deferred work.

## Validation Checklist

- [ ] No new TS/lint errors in changed frontend files.
- [ ] Existing backend test suite remains green.
- [ ] User-facing fallback states remain clear.
- [ ] No sensitive/debug internals leaked to UI.

## Completion Criteria

- Tranche goals completed and validated.
- Plan updated with what was executed.
- Deferred items explicitly recorded for next run.
