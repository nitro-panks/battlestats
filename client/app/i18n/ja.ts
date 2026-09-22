import type { StringKey } from './keys';

// Japanese. Partial by design — see ko.ts. Latin `Tier` is deliberate: that is
// how JP players write it (Tier10, T9), evidenced in the research doc.
export const ja: Partial<Record<StringKey, string>> = {
    'nav.selectRealm': 'サーバー選択',
    'nav.realmCurrent': 'サーバー: {realm}',
    'nav.language': '言語',
    'nav.languageCurrent': '言語: {language}',
    'nav.selectTheme': 'テーマ選択',
    'nav.searchPlayer': 'プレイヤー検索',
    'nav.searchClan': 'クラン検索',
    'nav.searchSubmit': '検索',
    'nav.themeLight': 'ライト',
    'nav.themeDark': 'ダーク',
    'nav.themeCurrent': 'テーマ: {label}',
    // Generic UI chrome, not a NEEDS-NATIVE-CHECK omission — see the matching
    // comment in ko.ts. 最近 is the everyday word (distinct from 直近, which the
    // research doc leaves unattested) and 見た carries no WoWS register risk.
    // It labels a row of player links.
    'footer.lastViewed': '最近見た:',
    'footer.leaveFeedback': 'フィードバックを送る',

    'insights.tabs.activity': 'アクティビティ',
    'insights.tabs.ships': '艦艇',
    'insights.tabs.profile': 'プロフィール',
    'insights.tabs.efficiency': '効率',
    'insights.tabs.ranked': 'ランク戦',
    'insights.tabs.clanBattles': 'クラン戦',

    'player.section.rankedSeasons': 'ランク戦シーズン',
    'player.section.randomBattlesByTier': 'Tier別ランダム戦',
    'player.section.windowActivityVsShipHistory': '期間の活動と艦艇の通算成績',

    // Composed-template blocker (see the research doc + spec's "Known traps"
    // section): the clauses below are resolved through t() in the components,
    // so these two multi-part templates can now ship translated.
    'landing.treemap.heading': '{realm}サーバーで最もプレイされた{bucket}{suffix}',
    'landing.treemap.ariaLabel': '{realm}サーバーで{windowPhrase}に最もプレイされた{bucket}を{view}として表示',
    'landing.treemap.topPct': '上位{pct}%',
    'landing.treemap.windowPhraseWithDays': '直近{days}日間の艦艇ランキング集計期間',
    'landing.treemap.windowPhraseNoDays': '艦艇ランキング集計期間',
    'landing.treemap.viewTreemap': 'ツリーマップ',
    'landing.treemap.viewScatterplot': '戦闘数と勝率の散布図',
    'landing.shipLeaderboard.heading': '艦艇リーダーボード{suffix}',
    'landing.shipLeaderboard.windowSuffix': '直近{days}日間',

    'landing.treemap.chartSectionLabel': 'サーバー艦艇チャート',
    'landing.treemap.chartViewGroup': 'チャート表示',
    'landing.treemap.toggleMap': 'ツリーマップ',
    'landing.treemap.togglePlot': '散布図',

    'shipClass.destroyers': '駆逐艦',
    'shipClass.cruisers': '巡洋艦',
    'shipClass.battleships': '戦艦',
    'shipClass.aircraftCarriers': '空母',
    'shipClass.submarines': '潜水艦',
    'shipClass.ships': '艦艇',

    'common.all': 'すべて',
    // Restored, fix round 1 (F3) — see en.ts's comment on this key.
    'common.clear': 'クリア',
    'common.tier': 'Tier',
    // Filter-bar wiring (2026-08-04, follow-on #2): 艦種, the ship-class
    // umbrella word — corpus-attested as a filter label on
    // asia.wows-numbers.com/ja/ships/ (see the research doc's Verified terms
    // table). No longer omitted — see the comment block at the bottom of
    // this file for how the omission note used to read.
    'common.type': '艦種',
    // Same 2026-08-04 corpus pass, same page, same dual role: 国家 labels
    // the nationality filter above the ranking table.
    'common.nation': '国家',
    // NEEDS-NATIVE-CHECK — shipped anyway (generic-chrome admission, not a
    // corpus attestation): 等級 ("grade") is this task's own rendering of
    // our badge taxonomy (Expert/I/II/III), not a wows-numbers or community
    // term for it. This is the weakest attestation in this change, flagged
    // deliberately rather than left to blend in with the rest.
    'common.award': '等級',
    // PlayerDetail's summary cards and header meta. Corpus pass 2026-08-11
    // against asia.wows-numbers.com/ja/player/<id>/ — its player summary table
    // carries the same stat rows as this card row, the closest register match
    // available.
    'player.stats.winRate': '勝率',
    // 生存率. **This reverses the 2026-08-11 value (生還) on a native-audit
    // finding, 2026-08-11 later the same day — the earlier comment here told
    // the next reader NOT to make this change, so read the reasoning before
    // reverting it back.** Both values are attested; they sit at different
    // TIERS of source. 生還 is what asia.wows-numbers.com's ko/ja localization
    // writes (`| 生還 | 39.28% |`). 生存率 is what the JP WoWS community writes
    // about its own stats: wikiwiki.jp/nanjwows/指標 carries the section
    // heading `### 生存率` (×2, and 生還 ×0 on that page), and a player blog
    // enumerating his own stat screen reads
    // "戦闘数/平均Tier/PR/勝数(勝率)/生存率/ダメージ/平均キル数/キルレシオ".
    // This project's Register target is the community's register, not a
    // localizer's, so the community source wins where they disagree.
    'player.stats.survival': '生存率',
    // The kill/death row on the same table (`| キル/デス比 | 1.16 |`, the
    // same value the KO page labels 격침 비율). Translated rather than left
    // Latin because, unlike WR/PR, the corpus never abbreviates this one.
    'player.stats.kdr': 'キル/デス比',
    // PvP/PvE stay Latin: neither appears as display text anywhere in the ja
    // corpus. WR/PR precedent + the verified noun 戦闘数.
    'player.stats.pvpBattles': 'PvP 戦闘数',
    'player.stats.pveBattles': 'PvE 戦闘数:',
    // NEEDS-NATIVE-CHECK: すべて is admitted generic chrome (common.all) and
    // 戦闘数 is verified, but the compound is this task's own composition.
    // The page's own "all battles" tab reads 総合, which is a mode name rather
    // than a count label, so it was not borrowed here.
    'player.stats.totalBattles': 'すべての戦闘数:',
    // Restructured deliberately, not calqued — noun first, time after the
    // colon, matching how the corpus writes recency (最近 / 直近N日).
    'player.header.lastPlayedToday': '最終戦闘: 本日',
    'player.header.lastPlayedDaysAgo': '最終戦闘: {days}日前',
    'player.header.updating': '更新中…',
    // 分後, not 分: bare 分 reads as a duration ("15 minutes"), 後 makes it the
    // deadline the English means ("in 15 min").
    'player.header.nextUpdate': '次の更新: {minutes}分後',
    'player.header.shareAriaLabel': 'プレイヤーURLをコピー',
    // 戦績 is the corpus's word for a player's record (Verified terms table).
    // NEEDS-NATIVE-CHECK on the second sentence — longer prose than anything
    // else in this dictionary.
    'player.hidden.title': 'このプレイヤーの戦績は非公開です。',
    'player.hidden.body': 'このプレイヤーはプロフィールを非公開に設定しています。詳細な統計とチャートは表示できません。',

    // Activity card. Pills are generic-chrome calendar words; the span headers
    // reuse 直近, already shipped in the landing window phrases.
    'battleHistory.window.day': '日',
    'battleHistory.window.week': '週',
    'battleHistory.window.month': '月',
    'battleHistory.window.ninety': '90日',
    'battleHistory.header.today': '本日',
    'battleHistory.header.last7': '直近7日間',
    'battleHistory.header.last30': '直近30日間',
    'battleHistory.header.last90': '直近90日間',
    'battleHistory.mode.random': 'ランダム戦',
    'battleHistory.mode.ranked': 'ランク戦',
    'battleHistory.tile.ships': '艦艇',
    'battleHistory.tile.avgDamage': '平均ダメージ',
    // 平均撃沈数. **Also reverses a 2026-08-11 value (艦船撃沈) — same audit,
    // and this one was a genuine ontology error, not a register call.**
    // 艦船撃沈 carries no per-battle sense of its own: it appears TWICE on the
    // source page with two different denotations — `| 艦船撃沈 | 0.7 |` under
    // the section header `| 期間平均値 |` (an average), and `艦船撃沈 / 10`
    // under `### 記録` beside 武蔵 (a raw single-battle count). The header is
    // doing the work. Our tile has no such header: it sits in a totals band
    // beside 戦闘数 38 and 艦艇 11, both raw counts, so the borrowed label
    // reads as "ships sunk, total". Every header-less form in the corpus leads
    // with 平均 — `平均撃破数` heads the per-ship column on that same page, and
    // wikiwiki.jp/nanjwows/指標 has the heading `### 平均撃沈数` (×2).
    // 撃沈 over 撃破 because it is the ship-specific verb.
    'battleHistory.tile.fragsPerBattle': '平均撃沈数',
    'battleHistory.strip.utcTitle': '日付はUTC基準です。戦闘は最初に検出された時点で記録されるため、日本時間午前9時より前の戦闘は前日の日付に表示されます。',

    'common.battles': '戦闘数',
    'common.avgDamage': '平均ダメージ',
    'common.winRate': '勝率',
    // 艦名 (ship NAME), not 艦艇 (ship as an object) — the audit's third
    // finding. This key's only call site is the ships-table column whose cells
    // hold names (Nagato, ARP Yamato), and the source corpus splits the two
    // senses on one page: `| 艦名 | Tier | 国家 | …` heads that column, while
    // 艦艇 is the object noun there (艦艇発見数 = ships spotted, 通常艦艇).
    // Korean needs no such split — its own table heads the same column 함선
    // (`|  | 함선 | 단계 | 국가 |`), so ko.ts keeps one word for both.
    // **If a future call site uses this key for the ship as an OBJECT rather
    // than its name, split the key** — 艦名 would be wrong there, and
    // battleHistory.tile.ships (a count) already uses 艦艇 for that reason.
    'common.ship': '艦名',
    'common.player': 'プレイヤー',
    'common.season': 'シーズン',

    'notFound.title': 'ページが見つかりません',
    'notFound.body': 'お探しのページは見つかりませんでした。',

    // FeedbackModal.tsx — generic UI chrome, see the research doc's admission
    // table. These are form labels/states, not WoWS vocabulary.
    'feedback.modal.title': 'フィードバックを送る',
    // "language issue" here means "our translation is wrong," so rendered as
    // 翻訳問題の報告 ("report of a translation problem") rather than a
    // literal calque of "language." Same register decision as ko.ts's
    // 번역 오류 신고 — see that file's NEEDS-NATIVE-CHECK comment.
    'feedback.category.languageIssue': '翻訳問題の報告',
    'feedback.category.featureSuggestion': '機能の提案',
    'feedback.category.bugReport': 'バグ報告',
    'feedback.messagePlaceholder': '問題や提案の内容を入力してください',
    'feedback.submit': '送信',
    'feedback.submitting': '送信中…',
    'feedback.cancel': 'キャンセル',
    'feedback.close': '閉じる',
    'feedback.success': 'ありがとうございます!いただいたフィードバックは確認を待っています。',
    'feedback.error.correctBelow': '以下のエラーを修正してください。',
    'feedback.error.generic': '問題が発生しました。しばらくしてからもう一度お試しください。',
    'feedback.error.network': 'ネットワークエラーです。もう一度お試しください。',

    // NEEDS-NATIVE-CHECK — same residue as ko.ts, key-for-key (the research
    // doc's gaps apply to both locales identically since it's one corpus
    // covering both). See ko.ts for the full reasoning per group; summary:
    //
    // Our coinage, no in-game/community term:
    //   player.section.efficiencyBadges  (see below for why the Efficiency
    //   TAB label ships anyway), insights.tabsAriaLabel
    //   battleHistory.tile.windowWr  (added 2026-08-11 — "window" as a span is
    //   our framing; 期間平均値 on the ja player table labels an averages
    //   block, not a window, and WR stays Latin by the documented rule)
    //
    // Explicitly "not verified" in the research doc:
    //   player.section.winRateVsSurvival  — HALF-RESOLVED 2026-08-11: the
    //   corpus pass attested the survival noun (生還, not the predicted
    //   生存率; see player.stats.survival above). The heading stays omitted
    //   for its unattested "vs" connective, like the compounds below.
    //
    // Compound headings — connective ("vs", "timeline") unattested:
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
    // reasoning on 効率 specifically — it's our own coinage in English too,
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
    // Out of scope by the spec's own rule, not by attestation gap — see
    // ko.ts for the full reasoning:
    //   landing.treemap.infoLabel
};
