# Runbook: Resolve Dependabot NPM Vulnerability Alerts

_Created: 2026-03-27_
_Status: Ready for implementation_

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

## Resolution Plan

### Step 1: Try `npm audit fix` (non-breaking)

```bash
cd client
npm audit fix
npm run build
npm run lint
npm test -- --runInBand
```

This will update transitive deps within their current semver ranges. If all three resolve, skip to Step 4.

### Step 2: Override stubborn transitive deps

If `npm audit fix` leaves residual vulnerabilities, add `overrides` in `client/package.json` to force patched versions:

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

Only override packages that `npm audit fix` didn't resolve. Test each override individually if possible.

### Step 3: If overrides cause breakage

The most likely breakage source is `tailwindcss@3.4.x`, which pins older transitive versions. If overrides cause issues:

1. Check if upgrading `tailwindcss` to 3.4.20+ or 4.x resolves the transitive deps naturally.
2. For `brace-expansion` in `eslint@9`, check if a minor eslint bump pulls in a fixed minimatch.
3. As a last resort, accept the moderate-severity dev-only alerts and document the risk acceptance in this runbook.

### Step 4: Validate

```bash
cd client
npm audit              # Should show 0 vulnerabilities
npm run build          # Production build succeeds
npm run lint           # ESLint passes
npm test -- --runInBand  # Jest tests pass
npx playwright test    # E2E tests pass (if browsers installed)
```

### Step 5: Commit and push

Stage only `client/package.json` and `client/package-lock.json`. The commit message should reference the Dependabot alerts being resolved.

## Advisories

- picomatch method injection: https://github.com/advisories/GHSA-3v7f-55p6-f55p
- picomatch ReDoS: https://github.com/advisories/GHSA-c2c7-rcm5-vvqj
- brace-expansion hang: https://github.com/advisories/GHSA-f886-m6hf-6m8v
- yaml stack overflow: https://github.com/advisories/GHSA-48c2-rrv3-qjmp
