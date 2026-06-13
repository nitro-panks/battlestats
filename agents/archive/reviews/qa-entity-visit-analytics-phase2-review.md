# QA Review — Entity Visit Analytics Phase 2

## Verdict

Proceed, with the tranche kept narrow exactly as the runbook defines it:

1. Django admin as the internal consumer,
2. rebuild and cleanup commands,
3. optional GA4 parallel emission only.

This is the right measured follow-on step after Phase 1.

## QA Findings

### 1. High: Rebuild command must be deterministic from raw events

If `rebuild_entity_visit_daily` can produce different totals from the same raw rows, the aggregate table becomes untrustworthy.

Required action:

1. Recompute every aggregate field from `EntityVisitEvent`, not from partial prior daily values.
2. Add tests for mixed entities and mixed dates.

### 2. Medium: Cleanup command must preserve aggregates

Deleting raw rows is acceptable only if the command makes it clear that daily aggregate rows are untouched.

Required action:

1. Ensure cleanup deletes only `EntityVisitEvent` rows.
2. Add dry-run output and a test for non-destructive preview behavior.

### 3. Medium: Admin registration should favor operability over completeness

The internal consumer should make analytics usable immediately. Dense admin pages with no filters or search will not help.

Required action:

1. Add list displays, basic search, and sensible filters.
2. Keep the admin scope read-leaning and operationally useful.

### 4. Medium: GA4 emission must remain optional and non-blocking

The first-party POST is the product’s source of truth. GA4 is only a parallel signal.

Required action:

1. Guard GA bootstrap behind an env var.
2. Keep first-party submission unchanged.
3. Do not fail route rendering if `gtag` is unavailable.

## Required QA Checks

1. `EntityVisitEvent` and `EntityVisitDaily` appear in Django admin.
2. Admin list views expose enough fields to inspect entity, date, count, and recency.
3. Rebuild command regenerates correct aggregate totals from raw rows.
4. Rebuild command dry-run does not write rows.
5. Cleanup command dry-run reports deletions without deleting rows.
6. Cleanup command deletes only raw event rows older than the requested threshold.
7. Existing daily aggregate rows remain after cleanup.
8. Client analytics still posts the first-party event after the GA changes.
9. When GA is not configured, no GA path is attempted.
10. When GA is configured and `window.gtag` exists, `entity_detail_view` is emitted with the expected fields.

## Regression Risks

1. Aggregate drift if the rebuild command forgets distinct-count semantics.
2. Destructive cleanup that removes the wrong rows or touches daily tables.
3. GA bootstrap introducing hydration or runtime noise in the app shell.
4. Client analytics helper becoming harder to test after GA is added.

## QA Recommendation

Ship this tranche only with focused command tests and at least one client-level assertion that first-party tracking remains intact after the GA extension.
