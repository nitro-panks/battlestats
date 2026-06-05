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

// ISO-8601 week number + week-year for a UTC timestamp. Used to label a season
// by its starting week, e.g. season 0 (Mon 11 May 2026) → { week: 20, year: 2026 }.
export function isoWeek(ms: number): { week: number; year: number } {
    const d = new Date(ms);
    const target = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
    const dayNr = (target.getUTCDay() + 6) % 7;          // Mon=0 … Sun=6
    target.setUTCDate(target.getUTCDate() - dayNr + 3);  // Thursday of this ISO week
    const year = target.getUTCFullYear();                // ISO week-year
    const firstThursday = new Date(Date.UTC(year, 0, 4));
    const firstDayNr = (firstThursday.getUTCDay() + 6) % 7;
    firstThursday.setUTCDate(firstThursday.getUTCDate() - firstDayNr + 3);
    const week = 1 + Math.round((target.getTime() - firstThursday.getTime()) / (7 * 86_400_000));
    return { week, year };
}

// Season week label, e.g. "WK20" or (with year) "WK20'26". `ms` is a season-start
// timestamp (ISO date-only strings parse as UTC midnight).
export function formatWeek(ms: number, withYear = false): string {
    const { week, year } = isoWeek(ms);
    // A season spans two ISO weeks; label it WK<n>-<n2> where n2 is the window's
    // second calendar week (computed, so year-boundary wraps like 52→1 are correct).
    const week2 = isoWeek(ms + 7 * 24 * 60 * 60 * 1000).week;
    const base = `WK${week}-${week2}`;
    return withYear ? `${base}'${String(year).slice(-2)}` : base;
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
