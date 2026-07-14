import React from 'react';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faSun, faMountainSun, faCircleHalfStroke, faCircle, faBed } from '@fortawesome/free-solid-svg-icons';
import type { IconDefinition } from '@fortawesome/fontawesome-svg-core';
import type { ActivityBucketKey } from './clanMembersShared';

// Rise-to-bed activity metaphor: a player's recency reads as a time of day.
// Bright sun (just played) → sunset (this month) → half moon (cooling) →
// full moon (dormant) → bed (gone dark). The icon replaces a raw "Nd idle"
// count because the phase is more legible at a glance than the number.
//
// Buckets + day thresholds mirror the backend (_classify_clan_member_activity)
// and the clan chart legend (ClanSVG getActivityBuckets); labels are kept in
// sync with that legend so the chart and the icon say the same thing.
interface ActivityStyle {
    icon: IconDefinition;
    color: string;
    label: string;
    detail: string;
}

const ACTIVITY_STYLES: Record<Exclude<ActivityBucketKey, 'unknown'>, ActivityStyle> = {
    active_7d: { icon: faSun, color: '#f59e0b', label: 'Active now', detail: 'battled within 7 days' },
    active_30d: { icon: faMountainSun, color: '#f97316', label: 'Still warm', detail: 'battled within 30 days' },
    cooling_90d: { icon: faCircleHalfStroke, color: '#818cf8', label: 'Cooling', detail: 'battled within 90 days' },
    dormant_180d: { icon: faCircle, color: '#64748b', label: 'Dormant', detail: 'battled within 180 days' },
    inactive_180d_plus: { icon: faBed, color: '#94a3b8', label: 'Gone dark', detail: 'inactive 180+ days' },
};

// Short one-word status labels for compact surfaces (the stacked rail's box
// header row). Kept separate from ACTIVITY_STYLES.label — that long label is
// used elsewhere (tooltips, clan chart legend) and must not change.
export const ACTIVITY_SHORT_LABEL: Record<Exclude<ActivityBucketKey, 'unknown'>, string> = {
    active_7d: 'Active',
    active_30d: 'Warm',
    cooling_90d: 'Cooling',
    dormant_180d: 'Cold',
    inactive_180d_plus: 'Asleep',
};

// Expose the per-bucket activity color so callers can tint their own text/marks
// to match the icon without duplicating the palette.
export const activityColor = (bucket: Exclude<ActivityBucketKey, 'unknown'>): string => ACTIVITY_STYLES[bucket].color;

const SIZE_CLASS = { header: 'text-sm', inline: 'text-[11px]', search: 'text-xs' } as const;

// Same thresholds as the backend classifier, so a surface that only carries
// days_since_last_battle (player detail, landing lists) lands in the same
// bucket the clan-members payload would have computed server-side.
export const activityBucketFromDays = (days: number | null | undefined): ActivityBucketKey => {
    if (days == null) return 'unknown';
    if (days <= 7) return 'active_7d';
    if (days <= 30) return 'active_30d';
    if (days <= 90) return 'cooling_90d';
    if (days <= 180) return 'dormant_180d';
    return 'inactive_180d_plus';
};

interface ActivityIconProps {
    // Provide an explicit bucket (clan members) or a day count to derive one.
    bucket?: ActivityBucketKey;
    daysSinceLastBattle?: number | null;
    size?: keyof typeof SIZE_CLASS;
}

const ActivityIcon: React.FC<ActivityIconProps> = ({ bucket, daysSinceLastBattle, size = 'inline' }) => {
    const resolved = bucket ?? activityBucketFromDays(daysSinceLastBattle);
    if (resolved === 'unknown') return null;
    const style = ACTIVITY_STYLES[resolved];
    const title = `${ACTIVITY_SHORT_LABEL[resolved]} — ${style.detail}`;

    return (
        <span title={title} aria-label={title} className="inline-flex items-center cursor-help">
            <FontAwesomeIcon
                icon={style.icon}
                className={SIZE_CLASS[size]}
                style={{ color: style.color }}
                aria-hidden="true"
            />
        </span>
    );
};

export default ActivityIcon;
