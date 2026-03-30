from __future__ import annotations

import logging
from datetime import datetime

from django.db import transaction

from warships.models import Player, PlayerAchievementStat, PlayerExplorerSummary, Snapshot


log = logging.getLogger("players")


def _merge_scalar_value(current, incoming):
    if incoming is None:
        return current
    if current is None:
        return incoming

    if isinstance(current, bool) and isinstance(incoming, bool):
        return current or incoming
    if isinstance(current, datetime) and isinstance(incoming, datetime):
        return max(current, incoming)
    if isinstance(current, (int, float)) and isinstance(incoming, (int, float)):
        return max(current, incoming)
    if isinstance(current, str) and isinstance(incoming, str):
        return current or incoming
    return current


def _merge_snapshot_rows(canonical: Player, duplicate: Player) -> None:
    canonical_by_date = {
        snapshot.date: snapshot
        for snapshot in Snapshot.objects.filter(player=canonical).order_by("date", "id")
    }

    for duplicate_snapshot in Snapshot.objects.filter(player=duplicate).order_by("date", "id"):
        canonical_snapshot = canonical_by_date.get(duplicate_snapshot.date)
        if canonical_snapshot is None:
            duplicate_snapshot.player = canonical
            duplicate_snapshot.save(update_fields=["player"])
            canonical_by_date[duplicate_snapshot.date] = duplicate_snapshot
            continue

        canonical_snapshot.battles = _merge_scalar_value(
            canonical_snapshot.battles,
            duplicate_snapshot.battles,
        )
        canonical_snapshot.wins = _merge_scalar_value(
            canonical_snapshot.wins,
            duplicate_snapshot.wins,
        )
        canonical_snapshot.survived_battles = _merge_scalar_value(
            canonical_snapshot.survived_battles,
            duplicate_snapshot.survived_battles,
        )
        canonical_snapshot.interval_battles = _merge_scalar_value(
            canonical_snapshot.interval_battles,
            duplicate_snapshot.interval_battles,
        )
        canonical_snapshot.interval_wins = _merge_scalar_value(
            canonical_snapshot.interval_wins,
            duplicate_snapshot.interval_wins,
        )
        canonical_snapshot.last_fetch = _merge_scalar_value(
            canonical_snapshot.last_fetch,
            duplicate_snapshot.last_fetch,
        )
        canonical_snapshot.battle_type = _merge_scalar_value(
            canonical_snapshot.battle_type,
            duplicate_snapshot.battle_type,
        )
        canonical_snapshot.save()
        duplicate_snapshot.delete()


def _merge_achievement_rows(canonical: Player, duplicate: Player) -> None:
    canonical_by_key = {
        (achievement.achievement_code, achievement.source_kind): achievement
        for achievement in PlayerAchievementStat.objects.filter(player=canonical).order_by("id")
    }

    for duplicate_achievement in PlayerAchievementStat.objects.filter(player=duplicate).order_by("id"):
        achievement_key = (
            duplicate_achievement.achievement_code,
            duplicate_achievement.source_kind,
        )
        canonical_achievement = canonical_by_key.get(achievement_key)
        if canonical_achievement is None:
            duplicate_achievement.player = canonical
            duplicate_achievement.save(update_fields=["player"])
            canonical_by_key[achievement_key] = duplicate_achievement
            continue

        canonical_achievement.achievement_slug = _merge_scalar_value(
            canonical_achievement.achievement_slug,
            duplicate_achievement.achievement_slug,
        )
        canonical_achievement.achievement_label = _merge_scalar_value(
            canonical_achievement.achievement_label,
            duplicate_achievement.achievement_label,
        )
        canonical_achievement.category = _merge_scalar_value(
            canonical_achievement.category,
            duplicate_achievement.category,
        )
        canonical_achievement.count = _merge_scalar_value(
            canonical_achievement.count,
            duplicate_achievement.count,
        )
        canonical_achievement.refreshed_at = _merge_scalar_value(
            canonical_achievement.refreshed_at,
            duplicate_achievement.refreshed_at,
        )
        canonical_achievement.save()
        duplicate_achievement.delete()


def _merge_explorer_summary(canonical: Player, duplicate: Player) -> None:
    canonical_summary = PlayerExplorerSummary.objects.filter(
        player=canonical).first()
    duplicate_summary = PlayerExplorerSummary.objects.filter(
        player=duplicate).first()

    if duplicate_summary is None:
        return

    if canonical_summary is None:
        duplicate_summary.player = canonical
        duplicate_summary.save(update_fields=["player"])
        return

    for field in canonical_summary._meta.fields:
        if field.name in {"id", "player"}:
            continue
        setattr(
            canonical_summary,
            field.name,
            _merge_scalar_value(
                getattr(canonical_summary, field.name),
                getattr(duplicate_summary, field.name),
            ),
        )

    canonical_summary.save()
    duplicate_summary.delete()


class BlockedAccountError(Exception):
    """Raised when attempting to create a Player for a blocklisted account."""

    def __init__(self, player_id: int):
        self.player_id = player_id
        super().__init__(f"Account {player_id} is blocklisted and cannot be created")


def get_or_create_canonical_player(player_id: int) -> tuple[Player, bool]:
    from warships.blocklist import is_account_blocked

    if is_account_blocked(player_id):
        raise BlockedAccountError(player_id)

    with transaction.atomic():
        matches = list(
            Player.objects.select_for_update().filter(player_id=player_id).order_by("id")
        )
        if not matches:
            return Player.objects.create(name="", player_id=player_id), True

        canonical = matches[0]
        duplicates = matches[1:]
        if not duplicates:
            return canonical, False

        duplicate_ids = [player.id for player in duplicates]
        log.warning(
            "Collapsing duplicate Player rows for player_id=%s into canonical id=%s; duplicate ids=%s",
            player_id,
            canonical.id,
            duplicate_ids,
        )

        for duplicate in duplicates:
            _merge_snapshot_rows(canonical, duplicate)
            _merge_achievement_rows(canonical, duplicate)
            _merge_explorer_summary(canonical, duplicate)
            duplicate.delete()

        return canonical, False
