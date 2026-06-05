// Fixed two-week "ship standings" seasons.
//
// The standings pivot from a rolling trailing 14 days to fixed, non-overlapping
// calendar fortnights anchored to ISO week 20 of 2026 — Monday 11 May 2026,
// 00:00 UTC. Each season is exactly 14 days; season N covers
// [epoch + N*14d, epoch + (N+1)*14d). This makes the schedule deterministic and
// identical for every player, and gives a well-defined "next window opens" time.
//
// IMPORTANT: when the backend snapshot/award job adopts fixed windows it MUST use
// this same epoch + length, so the data on the page matches the countdown shown.

export const SHIP_SEASON_EPOCH_MS = Date.UTC(2026, 4, 11); // Mon 11 May 2026, 00:00 UTC (month is 0-indexed)
export const SHIP_SEASON_LENGTH_MS = 14 * 24 * 60 * 60 * 1000;

export interface ShipSeasonWindow {
    index: number;   // 0 = W20-21 (11-24 May 2026)
    startMs: number; // inclusive
    endMs: number;   // exclusive — also the moment the next window opens
}

// The fixed season containing `nowMs`. Before the epoch this returns season 0.
export function currentShipSeason(nowMs: number): ShipSeasonWindow {
    const elapsed = nowMs - SHIP_SEASON_EPOCH_MS;
    const index = Math.max(0, Math.floor(elapsed / SHIP_SEASON_LENGTH_MS));
    const startMs = SHIP_SEASON_EPOCH_MS + index * SHIP_SEASON_LENGTH_MS;
    return { index, startMs, endMs: startMs + SHIP_SEASON_LENGTH_MS };
}

// Epoch ms at which the next window opens (== current window's exclusive end).
export function nextWindowOpenMs(nowMs: number): number {
    return currentShipSeason(nowMs).endMs;
}

// Season label from its [start, end) bounds, e.g. "11–24 May". `endMs` is the
// exclusive end (next window open), so the last included day is endMs - 1 day.
// Dates are formatted in UTC since seasons are UTC-anchored.
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

// Compact human duration, e.g. "2d 14h", "14h 03m", "07m". Floors to the minute.
export function formatCountdown(ms: number): string {
    if (ms <= 0) return 'now';
    const totalMin = Math.floor(ms / 60_000);
    const days = Math.floor(totalMin / (60 * 24));
    const hours = Math.floor((totalMin % (60 * 24)) / 60);
    const mins = totalMin % 60;
    const pad = (n: number) => String(n).padStart(2, '0');
    if (days > 0) return `${days}d ${pad(hours)}h`;
    if (hours > 0) return `${hours}h ${pad(mins)}m`;
    return `${pad(mins)}m`;
}
