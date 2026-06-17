# Runbook — Top players by random battles per realm (snapshot)

**Date:** 2026-06-16
**Status:** Active (point-in-time snapshot)
**Area:** player data / leaderboards

## Purpose

A point-in-time list of the **top 15 players by lifetime random (PvP) battles** on
each realm (NA / EU / ASIA), excluding hidden profiles. Useful as a hand-pick pool
for featured players, QA targets, or sanity-checking high-volume accounts.

## Method

Ranked by `Player.pvp_battles` (lifetime Wargaming `statistics.pvp.battles`, mapped
in `clan_crawl.py`), filtered `is_hidden=False`, partitioned by `Player.realm`.
Activity was **not** a filter — active or not, all included, per request.

```python
from warships.models import Player
Player.objects.filter(realm=realm, is_hidden=False).order_by('-pvp_battles')[:15]
```

**Caveats**
- Scope is the battlestats DB only (crawled population), **not** the entire WG
  population — these are the highest-volume accounts *we have stored*.
- Counts are lifetime random battles, regardless of recency.
- Player URLs are name-based (`/player/<urlencoded-name>?realm=<realm>`); a rename
  upstream would break the link until the next crawl.
- Snapshot date 2026-06-16; `pvp_battles` drifts upward as players keep playing, so
  rankings will shift over time. Re-run the query above to refresh.

## NA

| # | Random battles | Player |
|---|---|---|
| 1 | 81,012 | [Snipe2ndClass06](https://battlestats.online/player/Snipe2ndClass06?realm=na) |
| 2 | 66,237 | [Submarine_M1](https://battlestats.online/player/Submarine_M1?realm=na) |
| 3 | 65,962 | [KenF_1](https://battlestats.online/player/KenF_1?realm=na) |
| 4 | 62,394 | [Birdski_5051](https://battlestats.online/player/Birdski_5051?realm=na) |
| 5 | 59,293 | [CAZNA](https://battlestats.online/player/CAZNA?realm=na) |
| 6 | 59,082 | [CL121](https://battlestats.online/player/CL121?realm=na) |
| 7 | 58,718 | [RoyilusDrake](https://battlestats.online/player/RoyilusDrake?realm=na) |
| 8 | 58,576 | [BG45](https://battlestats.online/player/BG45?realm=na) |
| 9 | 57,600 | [ThottieBottie](https://battlestats.online/player/ThottieBottie?realm=na) |
| 10 | 57,210 | [Vanbeere](https://battlestats.online/player/Vanbeere?realm=na) |
| 11 | 56,498 | [mtn199](https://battlestats.online/player/mtn199?realm=na) |
| 12 | 55,527 | [Theodore_Lawson](https://battlestats.online/player/Theodore_Lawson?realm=na) |
| 13 | 54,200 | [Tomcat32](https://battlestats.online/player/Tomcat32?realm=na) |
| 14 | 53,869 | [WorstCaseScenario](https://battlestats.online/player/WorstCaseScenario?realm=na) |
| 15 | 52,423 | [griprite](https://battlestats.online/player/griprite?realm=na) |

## EU

| # | Random battles | Player |
|---|---|---|
| 1 | 87,199 | [ORP_Zaborze](https://battlestats.online/player/ORP_Zaborze?realm=eu) |
| 2 | 75,285 | [defreb](https://battlestats.online/player/defreb?realm=eu) |
| 3 | 75,175 | [Sodier_Of_Fortune](https://battlestats.online/player/Sodier_Of_Fortune?realm=eu) |
| 4 | 74,173 | [Dr_Doom___](https://battlestats.online/player/Dr_Doom___?realm=eu) |
| 5 | 73,934 | [ANASA_1](https://battlestats.online/player/ANASA_1?realm=eu) |
| 6 | 73,082 | [Hermann58](https://battlestats.online/player/Hermann58?realm=eu) |
| 7 | 70,270 | [dar_k](https://battlestats.online/player/dar_k?realm=eu) |
| 8 | 67,283 | [000nikas000](https://battlestats.online/player/000nikas000?realm=eu) |
| 9 | 66,387 | [Kadraaier](https://battlestats.online/player/Kadraaier?realm=eu) |
| 10 | 66,192 | [Mechwarrior12](https://battlestats.online/player/Mechwarrior12?realm=eu) |
| 11 | 62,691 | [ash004](https://battlestats.online/player/ash004?realm=eu) |
| 12 | 62,630 | [Lenoseas](https://battlestats.online/player/Lenoseas?realm=eu) |
| 13 | 62,518 | [HLIAS_M](https://battlestats.online/player/HLIAS_M?realm=eu) |
| 14 | 62,193 | [lazgou1](https://battlestats.online/player/lazgou1?realm=eu) |
| 15 | 60,737 | [JensUwe2](https://battlestats.online/player/JensUwe2?realm=eu) |

## ASIA

| # | Random battles | Player |
|---|---|---|
| 1 | 74,841 | [a24248048](https://battlestats.online/player/a24248048?realm=asia) |
| 2 | 70,188 | [Daniel_James_Tramacchi](https://battlestats.online/player/Daniel_James_Tramacchi?realm=asia) |
| 3 | 61,310 | [cdrom_hkcs](https://battlestats.online/player/cdrom_hkcs?realm=asia) |
| 4 | 60,236 | [BiubiuGuru](https://battlestats.online/player/BiubiuGuru?realm=asia) |
| 5 | 59,223 | [GS_F](https://battlestats.online/player/GS_F?realm=asia) |
| 6 | 59,120 | [yama_tami](https://battlestats.online/player/yama_tami?realm=asia) |
| 7 | 58,171 | [EvolTRx0UC_HivNexusZZ](https://battlestats.online/player/EvolTRx0UC_HivNexusZZ?realm=asia) |
| 8 | 57,334 | [tgj_1](https://battlestats.online/player/tgj_1?realm=asia) |
| 9 | 56,767 | [arigakousaku](https://battlestats.online/player/arigakousaku?realm=asia) |
| 10 | 53,940 | [mymylove1967](https://battlestats.online/player/mymylove1967?realm=asia) |
| 11 | 53,883 | [vip_k07](https://battlestats.online/player/vip_k07?realm=asia) |
| 12 | 53,617 | [Hamakasu_jp](https://battlestats.online/player/Hamakasu_jp?realm=asia) |
| 13 | 52,423 | [Legend777sp](https://battlestats.online/player/Legend777sp?realm=asia) |
| 14 | 51,962 | [miffy_x](https://battlestats.online/player/miffy_x?realm=asia) |
| 15 | 51,018 | [YYSandm](https://battlestats.online/player/YYSandm?realm=asia) |
