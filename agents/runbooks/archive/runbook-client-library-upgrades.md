# Runbook: Client Library Upgrades

_Last updated: 2026-03-15_

_Status: Active maintenance reference_

## Goal

Upgrade the main frontend libraries to the highest versions that fit the current app architecture and dependency graph without introducing avoidable migration work.

## Decision

Keep the app on the current compatibility lane:

- Next.js `15.x`
- React `18.x`
- Tailwind CSS `3.x`
- ESLint `8.x`

Upgrade to the latest safe versions within those lanes.

## Why This Lane

- Next.js `16.x` is publishable with the current Node and React floor, but it removes `next lint` and introduces framework-level migration work that is outside a dependency-only refresh.
- React `19.x` is not required for the current app and would widen the change surface without clear payoff for this repo.
- Tailwind CSS `4.x` is a larger config and styling migration than this maintenance pass should absorb.
- ESLint `10.x` is not the right target while the repo still uses the legacy Next.js lint integration path.

## Selected Upgrades

Main runtime libraries:

- `next` -> `15.5.12`
- `react` -> keep `18.3.1`
- `react-dom` -> keep `18.3.1`
- `spinners-react` -> `1.0.11`

Main build and typing libraries:

- `eslint-config-next` -> `15.5.12`
- `eslint` -> `8.57.1`
- `typescript` -> `5.9.3`
- `postcss` -> `8.5.8`
- `tailwindcss` -> `3.4.19`
- `@types/node` -> `20.19.37`
- `@types/react` -> `18.3.28`
- `@types/react-dom` -> `18.3.7`

Libraries intentionally not upgraded across major boundaries in this pass:

- `next` not to `16.x`
- `react` / `react-dom` not to `19.x`
- `tailwindcss` not to `4.x`
- `eslint` not to `9.x` or `10.x`

## Constraints

- `next lint` is still used by the repo. Next.js `16.x` removes that command.
- The current client config is minimal, so patch and minor updates inside the active majors are low risk.
- The Docker client image already satisfies the Node requirement for the selected versions.

## Pending Vulnerability

Current `npm audit` finding after the safe upgrade pass:

- package: `flatted`
- severity: `high`
- advisory: `GHSA-25h7-pfq9-p65f`
- impact: unbounded recursion DoS in `parse()` revive phase
- exposure in this repo: transitive dev-tooling dependency only

Dependency path:

- `eslint` -> `file-entry-cache` -> `flat-cache` -> `flatted`

Assessment:

- this is not part of the runtime browser bundle or production request path
- the upstream semver range already allows the fixed version (`flat-cache@3.2.0` depends on `flatted ^3.2.9`)
- the issue is therefore a stale lockfile resolution, not a blocked dependency graph

## Vulnerability Remediation Plan

1. Refresh the transitive lockfile resolution:
   - `cd client && npm audit fix`
2. Re-check audit status:
   - `cd client && npm audit --json`
   - expected result: no remaining vulnerabilities
3. Validate the affected toolchain path:
   - `cd client && npm run lint`
4. If `npm audit fix` ever starts proposing major package moves instead of the transitive `flatted` bump, stop and treat that as a separate maintenance item.

## Files

- `client/package.json`
- `client/package-lock.json`
- `client/Dockerfile`

## Validation

1. Refresh installed packages:
   - `cd client && npm install`
2. Verify the safe upgrade set is installed:
   - `cd client && npm outdated`
   - expected result: the selected packages show `Current == Wanted`; any remaining `Latest` deltas should only be intentional deferred major-version upgrades
3. Run lint:
   - `cd client && npm run lint`
4. Refresh the Dockerized client dependencies:
   - `docker compose run --rm --no-deps react-app npm install`
5. Run production build in the Dockerized client environment:
   - `docker compose run --rm --no-deps react-app npm run build`
6. Re-check audit status when dependency files change:
   - `cd client && npm audit --json`

## Follow-Up Runbook

If the repo later wants to move to Next.js `16.x`, do that as a separate migration item. That follow-up should include:

- replacing `next lint` with direct ESLint CLI usage
- reviewing any async request API usage
- validating that no removed Next.js `16` APIs or conventions are in use
- re-running the client build and smoke checks after the major upgrade
