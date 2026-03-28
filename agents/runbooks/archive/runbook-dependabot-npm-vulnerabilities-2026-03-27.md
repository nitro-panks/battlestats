# Runbook: Resolve Dependabot NPM Vulnerability Alerts

_Created: 2026-03-27_
_QA verified: 2026-03-27_
_Status: QA complete — ready to commit `package-lock.json`_

## Summary

GitHub Dependabot reports 8 alerts (5 moderate, 3 low) on the default branch. All are in `client/` npm dependencies. The Python backend (`server/`) has zero known vulnerabilities per `pipenv check`.

`npm audit` collapses these into 3 distinct vulnerable packages:

| Package | Severity | Vulnerable Range | Patched In | Parent Chain | Production? |
|---------|----------|-----------------|------------|-------------|-------------|
| **picomatch** | high | ≤2.3.1, 4.0.0–4.0.3 | 4.0.4+ | jest, eslint-config-next → tinyglobby, tailwindcss → chokidar | No (dev/build only) |
| **brace-expansion** | moderate | ≤1.1.12, 2.0.0–2.0.2, 4.0.0–5.0.4 | 1.1.13 / 2.0.3 / 5.0.5+ | eslint → minimatch, eslint-config-next → minimatch, tailwindcss → sucrase → glob | No (dev/build only) |
| **yaml** | moderate | 2.0.0–2.8.2 | 2.8.3+ | tailwindcss → postcss-load-config | No (build only) |

## Risk Assessment

All three packages are **devDependencies** or transitive build-time deps. None are bundled into the production Next.js output served to users. The practical risk is limited to:

- **picomatch ReDoS / method injection**: Could slow or crash Jest test runs if a malicious glob pattern were processed. Not exploitable in production.
- **brace-expansion hang**: Could hang the linter or build if processing a crafted brace pattern. Not exploitable in production.
- **yaml stack overflow**: Could crash the build if `postcss-load-config` parsed a deeply nested YAML config. Not exploitable in production.

Despite being low practical risk, resolving these keeps the security posture clean and removes Dependabot noise.

## QA Results

`npm audit fix` resolved all 3 vulnerabilities with no breaking changes. No overrides needed.

### Resolved versions

| Package | Before | After |
|---------|--------|-------|
| picomatch (v4 tree) | 4.0.3 | 4.0.4 |
| picomatch (v2 tree, tailwindcss → chokidar) | 2.3.1 | 2.3.2 |
| brace-expansion (eslint-config-next → minimatch) | 5.0.4 | 5.0.5 |
| brace-expansion (eslint → minimatch) | 1.1.12 | 1.1.13 |
| brace-expansion (tailwindcss → sucrase → glob) | 2.0.2 | 2.0.3 |
| yaml (tailwindcss → postcss-load-config) | 2.5.0 | 2.8.3 |

### Validation results

| Check | Result |
|-------|--------|
| `npm audit` | 0 vulnerabilities |
| `npm run build` | Passes — all static and dynamic routes generated |
| `npm run lint` | Passes (8 pre-existing warnings, 0 errors) |
| `npm test -- --runInBand` | 102 passed, 9 failed — **failures are pre-existing** (same count before and after fix; confirmed via `git stash` baseline) |

### What changed

Only `client/package-lock.json` — no changes to `package.json`. The fix updated 9 transitive packages within their existing semver ranges.

## Resolution Plan

### Step 1: `npm audit fix` (sufficient)

```bash
cd client
npm audit fix
```

This resolves all 3 packages. Steps 2 and 3 below are fallback paths that were **not needed** during QA.

### Step 2: Override stubborn transitive deps (not needed)

If `npm audit fix` leaves residual vulnerabilities in a future occurrence, add `overrides` in `client/package.json`:

```json
{
  "overrides": {
    "picomatch": "^4.0.4",
    "brace-expansion": "^2.0.3",
    "yaml": "^2.8.3"
  }
}
```

Then:

```bash
cd client
rm -rf node_modules package-lock.json
npm install
npm audit
```

### Step 3: If overrides cause breakage (not needed)

The most likely breakage source is `tailwindcss@3.4.x`, which pins older transitive versions. If overrides cause issues:

1. Check if upgrading `tailwindcss` to 3.4.20+ or 4.x resolves the transitive deps naturally.
2. For `brace-expansion` in `eslint@9`, check if a minor eslint bump pulls in a fixed minimatch.
3. As a last resort, accept the moderate-severity dev-only alerts and document the risk acceptance.

### Step 4: Validate

```bash
cd client
npm audit              # Should show 0 vulnerabilities
npm run build          # Production build succeeds
npm run lint           # ESLint passes (warnings are pre-existing)
npm test -- --runInBand  # 9 pre-existing test failures unrelated to deps
```

### Step 5: Commit and push

Stage only `client/package-lock.json`. No `package.json` changes are required.

## Pre-existing test failures (not related)

9 test failures in `PlayerDetail.test.tsx` and `PlayerDetailInsightsTabs.test.tsx` exist both before and after the dependency update. These are unrelated to the vulnerability fix and are tracked separately.

## Advisories

- picomatch method injection: https://github.com/advisories/GHSA-3v7f-55p6-f55p
- picomatch ReDoS: https://github.com/advisories/GHSA-c2c7-rcm5-vvqj
- brace-expansion hang: https://github.com/advisories/GHSA-f886-m6hf-6m8v
- yaml stack overflow: https://github.com/advisories/GHSA-48c2-rrv3-qjmp
