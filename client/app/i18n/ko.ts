import type { StringKey } from './keys';

// Korean. Partial by design: an untranslated string is ABSENT, not a copy of
// the English one. Terminology + register decisions (compact mode names,
// 데미지 over 피해량, 전적 over 통계) are evidenced in
// agents/work-items/i18n-terminology-research.md
export const ko: Partial<Record<StringKey, string>> = {
    'nav.selectRealm': '서버 선택',
    'nav.realmCurrent': '서버: {realm}',
    'nav.language': '언어',
    'nav.languageCurrent': '언어: {language}',
    'nav.selectTheme': '테마 선택',
    'nav.searchPlayer': '플레이어 검색',
    'nav.searchClan': '클랜 검색',
    'nav.searchSubmit': '검색',
    'nav.themeLight': '라이트',
    'nav.themeDark': '다크',
    'nav.themeCurrent': '테마: {label}',
    // Generic UI chrome, not a NEEDS-NATIVE-CHECK omission: this key arrived
    // with the landing recent-players list (v4.9.0), after the pass that sorted
    // every other key below, so it was never triaged. 최근 is corpus-attested
    // (research doc, Verified terms) and 조회 is ordinary interface vocabulary
    // with no WoWS register to get wrong. It labels a row of player links.
    'footer.lastViewed': '최근 조회:',
    'footer.leaveFeedback': '피드백 남기기',

    'insights.tabs.activity': '활동',
    'insights.tabs.ships': '함선',
    'insights.tabs.profile': '프로필',
    'insights.tabs.efficiency': '효율',
    'insights.tabs.ranked': '랭크전',
    'insights.tabs.clanBattles': '클랜전',

    'player.section.rankedSeasons': '랭크전 시즌',
    'player.section.randomBattlesByTier': '티어별 랜덤전',
    'player.section.shipsPlayedInWindow': '이 기간에 탄 함선',

    // Composed-template blocker (see the research doc + spec's "Known traps"
    // section): the clauses below are resolved through t() in the components,
    // so these two multi-part templates can now ship translated.
    'landing.treemap.heading': '{realm} 서버에서 가장 많이 플레이한 {bucket}{suffix}',
    'landing.treemap.ariaLabel': '{realm} 서버에서 {windowPhrase} 동안 가장 많이 플레이한 {bucket}을 {view} 형태로 표시',
    'landing.treemap.topPct': '상위 {pct}%',
    'landing.treemap.windowPhraseWithDays': '최근 {days}일간의 함선 순위 집계 기간',
    'landing.treemap.windowPhraseNoDays': '함선 순위 집계 기간',
    'landing.treemap.viewTreemap': '트리맵',
    'landing.treemap.viewScatterplot': '전투 수 대비 승률 산점도',
    'landing.shipLeaderboard.heading': '함선 리더보드{suffix}',
    'landing.shipLeaderboard.windowSuffix': '최근 {days}일',

    'landing.treemap.chartSectionLabel': '서버 함선 차트',
    'landing.treemap.chartViewGroup': '차트 보기',
    'landing.treemap.toggleMap': '트리맵',
    'landing.treemap.togglePlot': '산점도',

    'shipClass.destroyers': '구축함',
    'shipClass.cruisers': '순양함',
    'shipClass.battleships': '전함',
    'shipClass.aircraftCarriers': '항공모함',
    'shipClass.submarines': '잠수함',
    'shipClass.ships': '함선',

    'common.all': '전체',
    // Restored, fix round 1 (F3) — see en.ts's comment on this key.
    'common.clear': '초기화',
    'common.tier': '티어',
    // Filter-bar wiring (2026-08-04, follow-on #2): 함종, the ship-class
    // umbrella word — corpus-attested as a filter label on
    // asia.wows-numbers.com/ko/ships/ (see the research doc's Verified terms
    // table). No longer omitted — see the comment block at the bottom of
    // this file for how the omission note used to read.
    'common.type': '함종',
    // Same 2026-08-04 corpus pass, same page, same dual role: 국가 labels
    // the nationality filter above the ranking table.
    'common.nation': '국가',
    // NEEDS-NATIVE-CHECK — shipped anyway (generic-chrome admission, not a
    // corpus attestation): 등급 ("grade") is this task's own rendering of
    // our badge taxonomy (Expert/I/II/III), not a wows-numbers or community
    // term for it. This is the weakest attestation in this change, flagged
    // deliberately rather than left to blend in with the rest.
    'common.award': '등급',
    // PlayerDetail's summary cards and header meta. Corpus pass 2026-08-11
    // against asia.wows-numbers.com/ko/player/<id>/ — the player summary table
    // there is the same four-or-five stat rows this card row shows, so it is
    // the closest possible register match.
    'player.stats.winRate': '승률',
    // 생존, NOT 생존율. The research doc's "Not verified" section predicted
    // 생존율 and found no hit; this pass found the real label, and it has no
    // 율 suffix — the site's stat row reads `| 생존 | 39.28% |`. Our card is
    // labelled "Survival" (not "Survival rate"), so the shorter form is also
    // the exact register match. Do not "correct" this to 생존율.
    //
    // UPHELD by the 2026-08-11 native audit, on stronger evidence than it
    // shipped with: 생존 there is NOT under a block header supplying "rate" —
    // it sits in the top block beside 전투/승률/PR as a bare label with a %
    // value, reproduced across four player pages on two hosts. That is
    // structurally identical to our card. (The community does write 생존률 in
    // prose — arca.live "게임 전체 평균 생존률이 50프로 언저리" — but that is
    // prose register, not label register.)
    // **Deliberately divergent from ja.ts, which moved to 生存率.** Not an
    // oversight to harmonize: Japanese's best LABEL-register source (the
    // wikiwiki metrics page, heading `### 生存率`) disagrees with its
    // localization site, while Korean's label-register evidence agrees with
    // its own. Each locale follows its own best source.
    'player.stats.survival': '생존',
    // The kill/death row on the same table (`| 격침 비율 | 1.16 |`, the same
    // value the JA page labels キル/デス比). Our English shows the Latin
    // abbreviation KDR; the corpus never abbreviates this one, unlike WR/PR,
    // so it is translated rather than left Latin.
    'player.stats.kdr': '격침 비율',
    // PvP/PvE stay Latin: neither appears as display text anywhere in the ko
    // corpus (the only hits are URL query values, `?type=cpvp`). That is the
    // WR/PR precedent — an abbreviation the community reads untranslated —
    // combined with the verified noun 전투 수.
    'player.stats.pvpBattles': 'PvP 전투 수',
    'player.stats.pveBattles': 'PvE 전투 수:',
    // NEEDS-NATIVE-CHECK CLEARED by the 2026-08-11 native audit: 전체 is not
    // merely our admitted chrome word, it is the site's own "all" column
    // header on this very table (`전체, 최근 ; 전투, 17, -`), and 전투 수 is
    // verified. 총 전투 수 is equally natural; no reason to switch.
    'player.stats.totalBattles': '전체 전투 수:',
    // Restructured deliberately, not calqued: English leads with the verb
    // ("Last played …"), Korean leads with the noun and puts the time after a
    // colon, which is how the corpus writes the same idea (최근 전적).
    'player.header.lastPlayedToday': '마지막 전투: 오늘',
    'player.header.lastPlayedDaysAgo': '마지막 전투: {days}일 전',
    'player.header.updating': '업데이트 중…',
    // 분 후, not 분: bare 분 denotes a duration, 후 makes it the time-until
    // the English means. Both audits flagged this independently in their own
    // language (ja took 分後 for the same reason).
    'player.header.nextUpdate': '다음 업데이트: {minutes}분 후',
    'player.header.shareAriaLabel': '플레이어 URL 복사',
    // 전적 is the corpus's word for a player's record (Verified terms table);
    // 통계 is the formal register, used here for "detailed statistics" where
    // the sentence is institutional anyway. NEEDS-NATIVE-CHECK on the second
    // sentence — it is longer prose than anything else in this dictionary.
    'player.hidden.title': '이 플레이어의 전적은 비공개입니다.',
    'player.hidden.body': '플레이어가 프로필을 비공개로 설정했습니다. 상세 통계와 차트는 볼 수 없습니다.',

    // Activity card. Pills are generic-chrome calendar words; the span headers
    // reuse 최근, the corpus's own recency word (Verified terms table).
    'battleHistory.window.day': '일',
    'battleHistory.window.week': '주',
    'battleHistory.window.month': '월',
    'battleHistory.window.fortyfive': '45일',
    'battleHistory.window.sixty': '60일',
    'battleHistory.window.seventyfive': '75일',
    'battleHistory.header.today': '오늘',
    'battleHistory.header.last7': '최근 7일',
    'battleHistory.header.last30': '최근 30일',
    'battleHistory.header.last45': '최근 45일',
    'battleHistory.header.last60': '최근 60일',
    'battleHistory.header.last75': '최근 75일',
    // Compact battle-mode forms, the resolved fork: what players write and
    // what wows-numbers' own tables use.
    'battleHistory.mode.random': '랜덤전',
    'battleHistory.mode.ranked': '랭크전',
    // 함선 수, parallel to the attested 전투 수 beside it: this tile is a
    // COUNT of distinct ships, not the ship noun. Our own composition (no
    // corpus analogue — wows-numbers has no such tile), admitted as generic
    // chrome on the strength of the 전투 수 pattern.
    'battleHistory.tile.ships': '함선 수',
    'battleHistory.tile.avgDamage': '평균 데미지',
    // 평균 격침. **This reverses the 2026-08-11 value (함선 격침) on a native
    // audit the same day — read this before "restoring" the older citation,
    // which is real but describes a different position on the page.**
    // wows-numbers ko uses TWO labels for this one metric, chosen by whether a
    // block header is present:
    //   header present → `| 전투 평균치 |` … `| 함선 격침 | 0.7 |` (an average)
    //   header absent  → `평균 격침`, four times as a bare column header:
    //                    `| 전투 | 전투 % | 승률 | PR | 평균 격침 | 평균 데미지 |`
    // The same page also uses `함선 격침` for a RAW single-battle record count,
    // which is what settles it: that form carries no per-battle sense of its
    // own. Our tile has no header — it sits beside 전투 수 38 and 함선 수 11,
    // both raw counts — so it needs the header-free form. Bonus: 평균 격침 vs
    // 격침 비율 (our KDR card) is exactly the contrast pair wows-numbers itself
    // renders as two adjacent columns.
    'battleHistory.tile.fragsPerBattle': '평균 격침',

    'common.battles': '전투 수',
    'common.avgDamage': '평균 데미지',
    'common.winRate': '승률',
    'common.ship': '함선',
    'common.player': '플레이어',
    'common.season': '시즌',

    'notFound.title': '페이지를 찾을 수 없습니다',
    'notFound.body': '요청하신 페이지를 찾을 수 없습니다.',

    // FeedbackModal.tsx — generic UI chrome, see the research doc's admission
    // table. These are form labels/states, not WoWS vocabulary.
    'feedback.modal.title': '피드백 남기기',
    // NEEDS-NATIVE-CHECK: "language issue" here means "our translation is
    // wrong," so rendered as "번역 오류 신고" (report a translation error)
    // rather than a literal calque of "language" (언어 문제). A native
    // speaker should confirm this reads as intended in this exact context —
    // flagged deliberately, same precedent as common.award.
    'feedback.category.languageIssue': '번역 오류 신고',
    'feedback.category.featureSuggestion': '기능 제안',
    'feedback.category.bugReport': '버그 신고',
    'feedback.messagePlaceholder': '문제나 제안 사항을 알려주세요',
    'feedback.submit': '제출',
    'feedback.submitting': '제출 중…',
    'feedback.cancel': '취소',
    'feedback.close': '닫기',
    'feedback.success': '감사합니다! 피드백이 검토를 기다리고 있습니다.',
    'feedback.error.correctBelow': '아래 오류를 수정해 주세요.',
    'feedback.error.generic': '문제가 발생했습니다. 나중에 다시 시도해 주세요.',
    'feedback.error.network': '네트워크 오류입니다. 다시 시도해 주세요.',

    // NEEDS-NATIVE-CHECK — every key below is omitted, not guessed. Grouped by
    // why the research doc doesn't clear it. `en.ts` grew past the brief's
    // dictionary listing (Task 6b + others); these keys are the reconciled
    // superset, not just the brief's original residue.
    //
    // Our own product coinage, no in-game/community term exists:
    //   player.section.efficiencyBadges  ("efficiency" isn't a WoWS term — see
    //   below for why the Efficiency TAB label ships anyway)
    //   insights.tabsAriaLabel  ("insights" as a UI concept is ours, not WG's)
    //   battleHistory.tile.windowWr  (added 2026-08-11 — "window" as a span is
    //   our framing; the ko player table's nearest block reads 전투 평균치,
    //   a different idea, and WR itself stays Latin by the documented rule)
    //
    // Flagged "not verified" in the research doc directly:
    //   player.section.winRateVsSurvival  — HALF-RESOLVED 2026-08-11: the
    //   corpus pass attested the survival noun after all (생존, not the
    //   predicted 생존율; see player.stats.survival above). This heading stays
    //   omitted for the OTHER reason — its "vs" connective, unattested like
    //   the rest of the compound headings below.
    //
    // Compound headings whose connective isn't attested (nouns are covered
    // individually, but "vs" and "timeline" as constructions are not):
    //   player.section.rankedGamesVsWinRate, player.section.clanBattlesVsWinRate,
    //   player.section.rankedSeasonTimeline, player.section.clanSeasonTimeline,
    //   player.section.battlesPlayedDistribution
    //
    // RESOLVED (composed-template blocker, follow-on #1): landing.treemap.heading,
    // landing.treemap.ariaLabel, and landing.shipLeaderboard.heading used to sit
    // here — either "word order too risky" or, for the ship-leaderboard heading,
    // a hardcoded English literal (`· last N days rolling`) built in
    // ShipLeaderboard.tsx that never passed through t(). Every interpolated
    // clause (the ship-class plurals, "top N%", the window phrase, the
    // map/plot view name, and the "last N days rolling" suffix) now has its own
    // key, resolved through t() in the component before being handed to the
    // outer template as a var — see landing.treemap.topPct/windowPhraseWithDays/
    // windowPhraseNoDays/viewTreemap/viewScatterplot, landing.shipLeaderboard.
    // windowSuffix, and shipClass.* above. All three templates ship translated.
    //
    // Generic UI chrome outside the research doc's WoWS-jargon remit. Two-tier
    // standard (see the research doc's "Generic UI chrome" section): everyday
    // interface vocabulary with no game-specific register risk may use
    // standard translations even without a corpus hit — nav.selectRealm,
    // nav.language, nav.selectTheme, common.all, nav.searchSubmit,
    // nav.themeLight/nav.themeDark/nav.themeCurrent above are that tier, and
    // — this fix round, closing an inconsistency where the standard was
    // applied to later keys but never backfilled to these three tab labels —
    // so are insights.tabs.activity/insights.tabs.profile/
    // insights.tabs.efficiency and notFound.title/notFound.body: "Activity",
    // "Profile" and "Efficiency" are everyday interface words carrying no
    // WoWS register risk (see the research doc's admission table for the
    // reasoning on 효율 specifically — it's our own coinage in English too,
    // and a consistently-translated tab strip beats one that alternates
    // languages tab to tab). common.close/common.clan were deleted outright
    // this fix round rather than kept as unwired scaffolding — no call site
    // references either anywhere in the client, and no follow-on task claims
    // them (contrast the surviving common.* keys, which Fix 4's spec
    // amendment assigned to a named follow-on — now wired, see
    // common.type/common.nation/common.award above). common.clear was
    // ALSO deleted in that round on the same "no call site" reasoning —
    // **that inference was wrong, corrected fix round 1**: no call site is
    // not the same as no owner. EfficiencyBadgeTable.tsx's filter-row Clear
    // button was always there, rendering the same hardcoded 'Clear' literal
    // this key would have driven; it just hadn't been wired yet, the same
    // unwired state common.type/common.nation/common.award were in before
    // this pass. Restored above with the key populated and the button wired.
    //
    // Out of scope by the spec's own rule, not by attestation gap — the
    // long info-tooltip paragraph this label opens is explicitly excluded
    // from localization (client-locale-toggle-spec.md's Scope section), so
    // translating just the trigger's accessible name would announce a
    // Korean label for an English panel:
    //   landing.treemap.infoLabel
};
