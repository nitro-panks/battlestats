# Runbook: Clan Activity Histogram

## Goal

Add a first-draft clan activity chart to the clan detail view so a roster's live core and dormant tail are visible at a glance.

## Product Framing

The clan page already shows battle volume and win rate, but those views can flatter a dead roster. This chart answers a different question:

- how many members are still playing now
- how many are cooling off
- how much of the roster is effectively dormant

It uses the field the product already stores reliably for each player:

- `days_since_last_battle`

## Chart Design

The first draft is a compact histogram of current inactivity bands.

- `0-7d`: active now
- `8-30d`: still warm
- `31-90d`: cooling
- `91-180d`: dormant
- `181d+`: gone dark
- `unknown`: recency unavailable

Bar height is roster count.

Hover detail shows the roster slice and the average win rate for that inactivity band. That keeps the main view clean while still letting the user inspect whether the active core is also the strong core.

## Design Principles

- Keep the chart compact and comparison-first.
- Use direct bucket labels instead of decorative framing.
- Let color encode recency severity, not ornament.
- Keep support text outside the plotting area.
- Avoid pretending we have a full clan activity time series when we only have current recency.

## API Contract

Endpoint:

- `GET /api/fetch/clan_members/<clan_id>/`

Additional fields used by the chart:

- `days_since_last_battle`
- `activity_bucket`

`activity_bucket` values:

- `active_7d`
- `active_30d`
- `cooling_90d`
- `dormant_180d`
- `inactive_180d_plus`
- `unknown`

## Files

- `agents/designer.md`
- `server/warships/serializers.py`
- `server/warships/tests/test_views.py`
- `client/app/components/ClanActivityHistogram.tsx`
- `client/app/components/ClanDetail.tsx`
- `client/app/components/ClanMembers.tsx`

## Validation

1. Run targeted backend tests:
   - `docker compose exec -T server python manage.py test warships.tests.test_views.ClanMembersEndpointTests`
2. Run the client build:
   - `cd client && npm run build`
3. Manual UI check on a populated clan:
   - verify the activity chart renders above the existing scatterplot
   - verify hover text matches the names and counts in each inactivity band
   - verify member pills show recency text in the roster list
   - verify the chart still reads on narrow widths without clipping

## Risks

- The chart reflects current inactivity, not longitudinal churn.
- Some players may carry stale recency if upstream refreshes lag.
- Very small clans may produce sparse bars; that is expected and still informative.

## Non-Goals

- Do not infer a true monthly clan-retention history from current player records.
- Do not replace the existing clan scatterplot in this pass.
- Do not redesign the clan members section beyond exposing recency clearly.