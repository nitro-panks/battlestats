from __future__ import annotations

from typing import Optional


PLAYSTYLE_RECRUIT_BATTLES_THRESHOLD = 100
PLAYSTYLE_SUPER_UNICUM_WR_THRESHOLD = 65.0
PLAYSTYLE_UNICUM_WR_THRESHOLD = 60.0
PLAYSTYLE_GREAT_WR_THRESHOLD = 56.0
PLAYSTYLE_GOOD_WR_THRESHOLD = 54.0
PLAYSTYLE_ABOVE_AVERAGE_WR_THRESHOLD = 52.0
PLAYSTYLE_AVERAGE_WR_THRESHOLD = 50.0
PLAYSTYLE_BELOW_AVERAGE_WR_THRESHOLD = 45.0
PLAYSTYLE_LOW_SURVIVABILITY_THRESHOLD = 33.0


def compute_player_verdict(
    pvp_battles: int,
    pvp_ratio: Optional[float],
    pvp_survival_rate: Optional[float],
) -> Optional[str]:
    if pvp_battles < PLAYSTYLE_RECRUIT_BATTLES_THRESHOLD:
        return 'Recruit'

    if pvp_ratio is None:
        return None

    if pvp_ratio > PLAYSTYLE_SUPER_UNICUM_WR_THRESHOLD:
        return 'Sealord'

    if pvp_survival_rate is None:
        return None

    is_low_survivability = pvp_survival_rate < PLAYSTYLE_LOW_SURVIVABILITY_THRESHOLD

    if pvp_ratio >= PLAYSTYLE_UNICUM_WR_THRESHOLD:
        return 'Kraken' if is_low_survivability else 'Assassin'

    if pvp_ratio >= PLAYSTYLE_GREAT_WR_THRESHOLD:
        return 'Daredevil' if is_low_survivability else 'Stalwart'

    if pvp_ratio >= PLAYSTYLE_GOOD_WR_THRESHOLD:
        return 'Raider' if is_low_survivability else 'Warrior'

    if pvp_ratio >= PLAYSTYLE_ABOVE_AVERAGE_WR_THRESHOLD:
        return 'Jetsam' if is_low_survivability else 'Survivor'

    if pvp_ratio >= PLAYSTYLE_AVERAGE_WR_THRESHOLD:
        return 'Drifter' if is_low_survivability else 'Flotsam'

    if pvp_ratio >= PLAYSTYLE_BELOW_AVERAGE_WR_THRESHOLD:
        return 'Potato' if is_low_survivability else 'Pirate'

    return 'Leroy Jenkins' if is_low_survivability else 'Hot Potato'