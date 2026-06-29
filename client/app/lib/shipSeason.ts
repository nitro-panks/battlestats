// Ship-standings window label formatter.
//
// The ship standings (treemap, /ship leaderboards, profile medals) are a rolling
// trailing 30-day window recomputed nightly — there is no fixed "season" anymore
// (the fixed-fortnight model was retired 2026-06-15). This module keeps the one
// helper still needed: a UTC date-range label for that window's [start, end)
// bounds, e.g. "11–24 May". Dates are formatted in UTC since the window bounds
// are UTC-anchored (the backend buckets by UTC date).

// Range label from [start, end) bounds, e.g. "11–24 May". `endMs` is the
// exclusive end (== the snapshot's captured_on), so the last included day is
// endMs - 1 day.
export function formatSeasonLabel(startMs: number, endMs: number): string {
    const start = new Date(startMs);
    const lastDay = new Date(endMs - 24 * 60 * 60 * 1000);
    const day = (d: Date) => d.getUTCDate();
    const mon = (d: Date) => d.toLocaleDateString(undefined, { month: 'short', timeZone: 'UTC' });
    const sameMonth = start.getUTCMonth() === lastDay.getUTCMonth();
    return sameMonth
        ? `${day(start)}–${day(lastDay)} ${mon(lastDay)}`
        : `${day(start)} ${mon(start)} – ${day(lastDay)} ${mon(lastDay)}`;
}
