# WoWS `account/statsbydate` Status

Last verified: 2026-04-02

## Why This Matters

This repo originally explored the World of Warships `account/statsbydate` endpoint as a source of per-day player battle activity. The endpoint is still documented by Wargaming, but current live behavior does not provide usable PvP-by-date slices for tested public accounts. Future work should start from this write-up instead of redoing the same endpoint validation cold.

## Current Conclusion

- The endpoint is still documented in the Wargaming Developer Room.
- The documentation UI is brittle when the docs path is loaded with query parameters.
- The live API endpoint is reachable and returns JSON.
- For tested public accounts on NA, EU, and Asia, `account/statsbydate` returned `status: ok` but `pvp: null`.
- Adding `extra=pve` still returned `pve: null` in live checks.
- Current working assumption: the endpoint is not reliable for product use, even though it has not been formally removed from the reference docs.

## Evidence

### 1. Documentation still exists

Base reference page:

`https://developers.wargaming.net/reference/all/wows/account/statsbydate/`

Observed on 2026-04-02:

- Page title is `PLAYER STATISTICS BY DATE`.
- Documented purpose: `Method returns statistics slices by dates in specified time span.`
- Documented hosts include `https://api.worldofwarships.eu/wows/account/statsbydate/`.
- Documented params include `application_id`, `account_id`, `dates`, `extra`, `fields`, `language`.
- `dates` limitation shown in docs: max 10 dates, within a 28-day range from current date.

### 2. Docs path with query string is still not trustworthy

Tested URL:

`https://developers.wargaming.net/reference/all/wows/account/statsbydate/?application_id=1a167f0d986f26f3fa7f792857b40151&r_realm=eu`

Observed on 2026-04-02:

- The base docs page still exists, but the docs site remains a poor source of truth when query parameters are mixed into the documentation path.
- Treat the real API host responses as authoritative for endpoint verification.

### 3. Live `account/info` still works for public accounts

Tested real regional hosts:

- `eu`
- `com` for NA
- `asia`

Sample public accounts tested on 2026-04-02:

- NA: `1001162884` (`deathdemon67`)
- EU: `501279789` (`commandernicson`)
- Asia: `2000257632` (`test00110`)

Observed on the live regional `account/info` hosts:

- Valid public data returned.
- `hidden_profile` was `false`.
- `statistics.pvp.battles` was nonzero.
- This remained true across NA, EU, and Asia sample accounts.

Representative response for `1001162884`:

```json
{
  "status": "ok",
  "meta": { "count": 1, "hidden": null },
  "data": {
    "1001162884": {
      "hidden_profile": false,
      "statistics": { "pvp": { "battles": 39043 } },
      "nickname": "deathdemon67",
      "last_battle_time": 1775174488
    }
  }
}
```

Representative EU response for `501279789`:

```json
{
  "status": "ok",
  "meta": { "count": 1, "hidden": null },
  "data": {
    "501279789": {
      "hidden_profile": false,
      "statistics": { "pvp": { "battles": 9764 } },
      "nickname": "commandernicson",
      "last_battle_time": 1775084480
    }
  }
}
```

### 4. Live `account/statsbydate` still did not return usable PvP slices

Observed on the live regional `account/statsbydate` hosts for the same public accounts:

- Response status was `ok`.
- `data[account_id].pvp` was `null`.
- This remained true with explicit recent dates on NA, EU, and Asia.

Representative response:

```json
{
  "status": "ok",
  "meta": { "count": 1, "hidden": null },
  "data": { "1001162884": { "pvp": null } }
}
```

Explicit recent-date test also returned null:

```text
https://api.worldofwarships.com/wows/account/statsbydate/?application_id=APP_ID&account_id=1001162884&dates=20260401,20260331,20260330
```

Observed response shape:

```json
{
  "status": "ok",
  "meta": { "count": 1, "hidden": null },
  "data": { "1001162884": { "pvp": null } }
}
```

Representative EU response shape:

```json
{
  "status": "ok",
  "meta": { "count": 1, "hidden": null },
  "data": { "501279789": { "pvp": null } }
}
```

Representative Asia response shape:

```json
{
  "status": "ok",
  "meta": { "count": 1, "hidden": null },
  "data": { "2000257632": { "pvp": null } }
}
```

### 5. `extra=pve` still did not return usable slices

Observed on 2026-04-02:

- Adding `extra=pve` did not restore the endpoint.
- The response returned both `pvp: null` and `pve: null`.

Representative response:

```json
{
  "status": "ok",
  "meta": { "count": 1, "hidden": null },
  "data": { "1001162884": { "pvp": null, "pve": null } }
}
```

## Reproduction Steps

Use the actual regional API host, not the documentation URL.

Check that the player exists and is public:

```bash
APP_ID=1a167f0d986f26f3fa7f792857b40151
curl -s "https://api.worldofwarships.com/wows/account/info/?application_id=$APP_ID&account_id=1001162884&fields=nickname,hidden_profile,last_battle_time,statistics.pvp.battles"
```

Then test `statsbydate`:

```bash
APP_ID=1a167f0d986f26f3fa7f792857b40151
curl -s "https://api.worldofwarships.com/wows/account/statsbydate/?application_id=$APP_ID&account_id=1001162884"
```

Then test explicit recent dates:

```bash
APP_ID=1a167f0d986f26f3fa7f792857b40151
curl -s "https://api.worldofwarships.com/wows/account/statsbydate/?application_id=$APP_ID&account_id=1001162884&dates=20260401,20260331,20260330"
```

Then test the documented extra field:

```bash
APP_ID=1a167f0d986f26f3fa7f792857b40151
curl -s "https://api.worldofwarships.com/wows/account/statsbydate/?application_id=$APP_ID&account_id=1001162884&extra=pve&dates=20260401,20260331,20260330"
```

## Implications For This Repo

- Do not treat `account/statsbydate` as a reliable source of daily PvP activity.
- Continue using snapshot-based derivation from stable cumulative account data for player activity and trend views.
- If `statsbydate` is ever reconsidered, treat it as opportunistic and feature-flagged until live validation proves otherwise.

## Open Questions / Next Checks

- Check for any official Wargaming forum or devblog statement that explicitly explains the null payload behavior.
- Test whether authenticated or private-account flows change the payload shape.
- Re-verify periodically before making any product decision that depends on historical daily slices.
