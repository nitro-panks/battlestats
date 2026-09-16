import type { StringKey } from './keys';

// Total by type: `en` can never have a hole. Every other dictionary is Partial.
export const en: Record<StringKey, string> = {
    'nav.selectRealm': 'Select realm',
    // The collapsed chip's accessible name — announces the CURRENT realm, not
    // just the affordance to change it (nav.selectRealm is the open listbox's
    // label, unchanged). Closes a recorded gap: the language chip didn't do
    // this either until nav.languageCurrent below.
    'nav.realmCurrent': 'Realm: {realm}',
    'nav.language': 'Language',
    // The collapsed chip's accessible name, mirroring nav.realmCurrent —
    // {language} is filled with the option's NATIVE name (English/한국어/日本語),
    // not a translated word for the language, matching how the open menu
    // already labels its rows (see LocaleSelector.tsx).
    'nav.languageCurrent': 'Language: {language}',
    'nav.searchPlayer': 'Search Players',
    'nav.searchClan': 'Search Clans',
    'nav.searchSubmit': 'Go',
    'nav.selectTheme': 'Select theme',
    'nav.themeLight': 'Light',
    'nav.themeDark': 'Dark',
    // Composed at runtime so the whole accessible name — not just the theme
    // word — is a single translated sentence; see ThemeToggle.tsx.
    'nav.themeCurrent': 'Theme: {label}',
    'footer.lastViewed': 'Last viewed:',
    // Third slot in the footer byline row, the exact slot the removed "Fork me
    // on GitHub" link used to occupy — see Footer.tsx.
    'footer.leaveFeedback': 'Leave feedback',

    'insights.tabs.activity': 'Activity',
    'insights.tabs.ships': 'Ships',
    'insights.tabs.profile': 'Profile',
    'insights.tabs.efficiency': 'Efficiency',
    'insights.tabs.ranked': 'Ranked',
    'insights.tabs.clanBattles': 'Clan Battles',
    'insights.tabsAriaLabel': 'Player insight tabs',

    'player.section.rankedGamesVsWinRate': 'Ranked Games vs Win Rate',
    'player.section.rankedSeasonTimeline': 'Ranked Season Timeline',
    'player.section.rankedSeasons': 'Ranked Seasons',
    'player.section.randomBattlesByTier': 'Random Battles by Tier',
    'player.section.windowActivityVsShipHistory': 'Window Activity vs Ship History',
    'player.section.winRateVsSurvival': 'Win Rate vs Survival',
    'player.section.battlesPlayedDistribution': 'Battles Played Distribution',
    'player.section.clanBattlesVsWinRate': 'Clan Battles vs Win Rate',
    'player.section.clanSeasonTimeline': 'Clan Season Timeline',
    'player.section.efficiencyBadges': 'Efficiency Badges',

    // PlayerDetail's four summary cards. Deliberately NOT reusing
    // common.winRate: that key holds sentence-case 'Win rate' and this card
    // renders TITLE case. Pointing the card at common.winRate would silently
    // restyle live English text as a side effect of a translation change —
    // the casing trap recorded in the locale spec's Known traps. Two keys,
    // one Korean/Japanese value each, is the honest way to keep English
    // byte-identical while the CJK renderings converge.
    'player.stats.winRate': 'Win Rate',
    'player.stats.pvpBattles': 'PvP Battles',
    'player.stats.kdr': 'KDR',
    'player.stats.survival': 'Survival',
    // The colon is part of the string: CJK punctuation and spacing around it
    // differ, so the separator belongs to the translator, not to the JSX.
    // Same precedent as footer.lastViewed.
    'player.stats.totalBattles': 'Total Battles:',
    'player.stats.pveBattles': 'PvE Battles:',

    // Header meta lines and the hidden-account notice. Generic UI chrome
    // (recency, refresh state, a privacy notice) — no WoWS jargon beyond
    // "battle"/"stats", both corpus-attested.
    'player.header.lastPlayedToday': 'Last played today',
    'player.header.lastPlayedDaysAgo': 'Last played {days} days ago',
    'player.header.updating': 'Updating…',
    'player.header.nextUpdate': 'Next update: {minutes} min',
    'player.header.shareAriaLabel': 'Copy shareable player URL',
    // Straight apostrophe, matching what the JSX &apos; entity rendered before
    // this key existed — English text must come through byte-identical.
    'player.hidden.title': 'This player\'s stats are hidden.',
    'player.hidden.body': 'The player has set their profile to private. Detailed statistics and charts are not available.',

    // Activity card. The window pills and their span header are two different
    // strings for the same span on purpose (pill '60d' vs header 'Last 60
    // days') — the pill is width-constrained, the header is not.
    'battleHistory.window.day': 'Day',
    'battleHistory.window.week': 'Week',
    'battleHistory.window.month': 'Month',
    'battleHistory.window.fortyfive': '45d',
    'battleHistory.window.sixty': '60d',
    'battleHistory.window.seventyfive': '75d',
    'battleHistory.header.today': 'Today',
    'battleHistory.header.last7': 'Last 7 days',
    'battleHistory.header.last30': 'Last 30 days',
    'battleHistory.header.last45': 'Last 45 days',
    'battleHistory.header.last60': 'Last 60 days',
    'battleHistory.header.last75': 'Last 75 days',
    'battleHistory.mode.random': 'Random Battles',
    'battleHistory.mode.ranked': 'Ranked',
    // Plural, so it cannot reuse common.ship ('Ship', the table column).
    'battleHistory.tile.ships': 'Ships',
    // 'Window' here is our own framing of the selected span, not a WoWS term —
    // untranslated in both locales, see the dictionaries' residue block.
    'battleHistory.tile.windowWr': 'Window WR',
    // Distinct from common.avgDamage ('Avg dmg', the narrow table column):
    // this tile has room for the unabbreviated word and shows it.
    'battleHistory.tile.avgDamage': 'Avg damage',
    'battleHistory.tile.fragsPerBattle': 'Frags/Battle',

    // Composed at runtime; word order differs per language, so the whole
    // sentence is one template rather than concatenated fragments. {bucket}
    // and {suffix} are themselves resolved through t() in the component
    // before being passed in — see landing.treemap.topPct/viewTreemap/
    // viewScatterplot/windowPhraseWithDays/windowPhraseNoDays and the
    // shipClass.* keys below (the composed-template blocker, closed).
    'landing.treemap.heading': '{realm} most-played {bucket}{suffix}',
    // The treemap SVG's accessible name (role="img" aria-label). Lifted verbatim
    // from RealmTopShipsTreemapSVG.tsx's prior hardcoded template so the
    // accessible name keeps pace with the visible <h2> above it, which already
    // went through t() in Task 6 — added in Task 6b, the gap that left this one
    // sentence untranslated in the same header row. {windowPhrase} and {view}
    // are resolved through t() in the component, not built as English literals.
    'landing.treemap.ariaLabel': '{realm} most-played {bucket} over the {windowPhrase}, shown as a {view}',
    // The " · top {pct}%" clause in landing.treemap.heading's {suffix}. Generic
    // UI chrome (no WoWS jargon), admitted per the research doc's two-tier
    // standard — see agents/work-items/i18n-terminology-research.md.
    'landing.treemap.topPct': 'top {pct}%',
    // The "over the {windowPhrase}" clause in landing.treemap.ariaLabel, and
    // (kept English-only, see RealmTopShipsTreemapSVG.tsx) the same wording in
    // the component's out-of-scope info-tooltip paragraph.
    'landing.treemap.windowPhraseWithDays': 'rolling, trailing {days}-day ship-standings window',
    'landing.treemap.windowPhraseNoDays': 'rolling ship-standings window',
    // The "{view}" clause in landing.treemap.ariaLabel.
    'landing.treemap.viewTreemap': 'treemap',
    'landing.treemap.viewScatterplot': 'battles-vs-win-rate scatterplot',
    // Live source (ShipLeaderboard.tsx) carries no realm in this heading — it's
    // always "Ship leaderboard", with an optional " · last N days rolling"
    // clause once the served window is known. {suffix} carries that clause,
    // itself built from landing.shipLeaderboard.windowSuffix resolved through
    // t() in the component (not an English literal).
    'landing.shipLeaderboard.heading': 'Ship leaderboard{suffix}',
    // The "last {days} days rolling" clause inside that suffix.
    'landing.shipLeaderboard.windowSuffix': 'last {days} days rolling',

    // The <section>'s aria-label. Generic chart chrome plus the corpus-attested
    // "ship" noun — see i18n-terminology-research.md.
    'landing.treemap.chartSectionLabel': 'Realm ship chart',
    // The map/plot toggle group's aria-label. Generic UI chrome, no WoWS
    // jargon (same tier as landing.treemap.viewTreemap/viewScatterplot).
    'landing.treemap.chartViewGroup': 'Chart view',
    // The two toggle-button labels themselves. Separate keys from
    // landing.treemap.viewTreemap/viewScatterplot (used in the aria-label's
    // "shown as a {view}" clause) because those values ("treemap",
    // "battles-vs-win-rate scatterplot") are not compact enough for a pill
    // button — reusing them here would either change the visible English text
    // (viewTreemap's English value is lowercase "treemap", not "Map") or wrap
    // a two-line label into a 28px-tall toggle. ko/ja reuse the SAME
    // vocabulary as viewTreemap/viewScatterplot (트리맵/ツリーマップ for the
    // map; a bare "scatterplot" word, not the full "battles vs win rate"
    // phrase, for the plot) rather than inventing new terms.
    'landing.treemap.toggleMap': 'Map',
    'landing.treemap.togglePlot': 'Plot',
    // Kept in en.ts/keys.ts (so the structure is complete for a future pass)
    // but DELIBERATELY OMITTED from ko.ts/ja.ts. This is the info-hint
    // button's long descriptive tooltip-trigger label; the tooltip panel it
    // opens is out of scope for localization per the client-locale-toggle
    // spec's Scope section (info-tooltip descriptions), and this label
    // names the same eligibility-window concept in the same register as
    // that untranslated prose — translating the trigger alone while the
    // panel it opens stays English would read as a broken promise, not a
    // partial win.
    'landing.treemap.infoLabel': 'About the ship treemap and its eligibility window',

    // Reusable ship-class vocabulary (plural form, for headings that name a
    // bucket of ships by class — not treemap-specific). The individual class
    // nouns are corpus-attested (see the research doc's Verified terms table);
    // neither ko nor ja pluralizes, so their values below equal the singular.
    'shipClass.destroyers': 'Destroyers',
    'shipClass.cruisers': 'Cruisers',
    'shipClass.battleships': 'Battleships',
    'shipClass.aircraftCarriers': 'Aircraft Carriers',
    'shipClass.submarines': 'Submarines',
    // Generic fallback when no class filter is active.
    'shipClass.ships': 'ships',

    'common.all': 'All',
    // Restored, fix round 1 (F3): deleted in an earlier fix round on the
    // (mistaken) grounds that no call site referenced it and no follow-on
    // claimed it — that was true of the code at the time, but not of the
    // intent: EfficiencyBadgeTable.tsx's filter-row Clear button always had
    // an owner, it just hadn't been wired yet. Generic UI chrome, same tier
    // as common.all — see the research doc's admission table.
    'common.clear': 'Clear',
    'common.tier': 'Tier',
    'common.type': 'Type',
    // The ship-nationality filter's label — corpus-attested 国家/국가, same
    // dual role (filter label + column header) as common.type — see
    // agents/work-items/i18n-terminology-research.md's Verified terms table.
    'common.nation': 'Nation',
    // EfficiencyBadgeTable's badge-tier filter label — our own product
    // taxonomy (Expert/I/II/III), not a WoWS term, so no corpus pass will
    // ever attest it. Admitted under the generic-chrome tier (see the
    // research doc's admission table) using 등급/等級 ("grade"), which is
    // what the column's values actually are. NEEDS-NATIVE-CHECK in ko.ts/
    // ja.ts: the weakest attestation in this change, flagged deliberately.
    'common.award': 'Award',
    'common.battles': 'Battles',
    'common.avgDamage': 'Avg dmg',
    'common.winRate': 'Win rate',
    'common.ship': 'Ship',
    'common.player': 'Player',
    'common.season': 'Season',

    'notFound.title': 'Page Not Found',
    'notFound.body': 'The requested page could not be found.',

    // FeedbackModal.tsx. Generic UI chrome (form labels/states, not WoWS
    // vocabulary) — admitted per the research doc's two-tier standard, see
    // agents/work-items/i18n-terminology-research.md.
    'feedback.modal.title': 'Leave feedback',
    // These three MUST send the backend's machine value (language_issue /
    // feature_suggestion / bug_report), never this display label — see
    // FeedbackModal.tsx's CATEGORY_LABEL_KEY map.
    'feedback.category.languageIssue': 'Report a language issue',
    'feedback.category.featureSuggestion': 'Suggest a feature',
    'feedback.category.bugReport': 'Report a bug',
    'feedback.messagePlaceholder': 'Describe the issue or suggestion',
    'feedback.submit': 'Submit',
    'feedback.submitting': 'Submitting…',
    'feedback.cancel': 'Cancel',
    // The × button's accessible name (aria-label), not visible text.
    'feedback.close': 'Close',
    'feedback.success': 'Thanks! Your feedback is queued for review.',
    'feedback.error.correctBelow': 'Please correct the errors below.',
    'feedback.error.generic': 'Something went wrong. Please try again later.',
    'feedback.error.network': 'Network error. Please try again.',
};
