/**
 * Ship class + nation display vocabulary — the single source of truth for the
 * ship leaderboard masthead (and any future ship-identity surface).
 *
 * The leaderboard payload carries `ship.ship_type` (the full WG class string,
 * e.g. "Destroyer") and `ship.nation` (a lowercase WG code, e.g. "japan").
 * `TypeSVG` renders the raw class string as a chart label and has no glyph
 * vocabulary, so this is a net-new shared map rather than an extension of it.
 *
 * Both lookups are null-safe: an absent/unknown class returns `null` (caller
 * omits the glyph cleanly — no placeholder), and an unknown nation falls back
 * to a capitalized form of the code rather than dropping the chip.
 */

export interface ShipClass {
    /** Compact WoWs-native class tag — DD / CA / BB / CV / SS. */
    abbr: string;
    /** Full class name for the chip text and the glyph tooltip. */
    label: string;
}

// Keyed by the exact `Ship.ship_type` strings the backend emits, plus the WG
// `AirCarrier` alias that `_SHIP_TYPE_ALIASES` normalizes to "Aircraft Carrier".
const SHIP_CLASS: Record<string, ShipClass> = {
    Destroyer: { abbr: 'DD', label: 'Destroyer' },
    Cruiser: { abbr: 'CA', label: 'Cruiser' },
    Battleship: { abbr: 'BB', label: 'Battleship' },
    'Aircraft Carrier': { abbr: 'CV', label: 'Aircraft Carrier' },
    AirCarrier: { abbr: 'CV', label: 'Aircraft Carrier' },
    Submarine: { abbr: 'SS', label: 'Submarine' },
};

export const shipClass = (shipType: string | null | undefined): ShipClass | null => {
    if (!shipType) return null;
    return SHIP_CLASS[shipType] ?? null;
};

// WG lowercase nation codes → readable labels.
const NATION_LABEL: Record<string, string> = {
    usa: 'USA',
    japan: 'Japan',
    germany: 'Germany',
    ussr: 'USSR',
    uk: 'U.K.',
    france: 'France',
    italy: 'Italy',
    pan_asia: 'Pan-Asia',
    europe: 'Europe',
    commonwealth: 'Commonwealth',
    netherlands: 'Netherlands',
    spain: 'Spain',
    pan_america: 'Pan-America',
};

export const nationLabel = (nation: string | null | undefined): string | null => {
    if (!nation) return null;
    const key = nation.toLowerCase().trim();
    if (!key) return null;
    return NATION_LABEL[key] ?? (key.charAt(0).toUpperCase() + key.slice(1));
};
