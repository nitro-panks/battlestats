import React from 'react';

export type EfficiencyRankTier = 'E' | 'I' | 'II' | 'III';

const toOrdinal = (value: number): string => {
    const absoluteValue = Math.abs(value);
    const mod100 = absoluteValue % 100;
    if (mod100 >= 11 && mod100 <= 13) {
        return `${value}th`;
    }

    switch (absoluteValue % 10) {
        case 1:
            return `${value}st`;
        case 2:
            return `${value}nd`;
        case 3:
            return `${value}rd`;
        default:
            return `${value}th`;
    }
};

const formatEfficiencyPercentile = (percentile: number | null | undefined): string => {
    if (percentile == null) {
        return 'Tracked-player efficiency rank available';
    }

    return `${toOrdinal(Math.round(percentile * 100))} percentile among eligible tracked players`;
};

const formatEfficiencyPopulation = (populationSize: number | null | undefined): string => {
    if (populationSize == null || populationSize <= 0) {
        return 'tracked-player field';
    }

    return `${populationSize.toLocaleString()} tracked players`;
};

const EFFICIENCY_TIER_META: Record<EfficiencyRankTier, { label: string; sigmaClassName: string; }> = {
    III: {
        label: 'Grade III',
        sigmaClassName: 'border-[#b87333] bg-[#fff1e6] text-[#8c4f1f] dark:border-[#a0522d] dark:bg-[#2a1a0e] dark:text-[#d4956a]',
    },
    II: {
        label: 'Grade II',
        sigmaClassName: 'border-[#94a3b8] bg-[#f8fafc] text-[#475569] dark:border-[#6e7f96] dark:bg-[#1c2433] dark:text-[#94a3b8]',
    },
    I: {
        label: 'Grade I',
        sigmaClassName: 'border-[#d4a72c] bg-[#fff7db] text-[#946200] dark:border-[#b8860b] dark:bg-[#2a2000] dark:text-[#d4a72c]',
    },
    E: {
        label: 'Expert',
        sigmaClassName: 'border-[#b91c1c] bg-[#fff1f2] text-[#991b1b] dark:border-[#dc2626] dark:bg-[#2a0a0a] dark:text-[#f87171]',
    },
};

export const resolveEfficiencyRankTier = (
    tier: EfficiencyRankTier | null | undefined,
    hasEfficiencyRankIcon: boolean | null | undefined,
): EfficiencyRankTier | null => tier ?? (hasEfficiencyRankIcon ? 'III' : null);

export const buildEfficiencyRankDescription = (
    tier: EfficiencyRankTier,
    percentile: number | null | undefined,
    populationSize: number | null | undefined,
): string => {
    const meta = EFFICIENCY_TIER_META[tier];
    const percentileText = formatEfficiencyPercentile(percentile);
    const populationText = formatEfficiencyPopulation(populationSize);
    return `Battlestats efficiency rank ${meta.label}: ${percentileText}. Based on stored WG badge profile for ${populationText}.`;
};

interface EfficiencyRankIconProps {
    tier: EfficiencyRankTier;
    percentile?: number | null;
    populationSize?: number | null;
    size?: 'header' | 'inline';
}

const EfficiencyRankIcon: React.FC<EfficiencyRankIconProps> = ({
    tier,
    percentile,
    populationSize,
    size = 'header',
}) => {
    const meta = EFFICIENCY_TIER_META[tier];
    const description = buildEfficiencyRankDescription(
        tier,
        percentile,
        populationSize,
    );

    if (size === 'inline') {
        return (
            <span
                title={description}
                aria-label={description}
                className={`inline-flex h-[0.95rem] w-[0.95rem] cursor-help items-center justify-center rounded-full border text-[0.62rem] font-bold leading-none ${meta.sigmaClassName}`}
            >
                Σ
            </span>
        );
    }

    return (
        <span
            title={description}
            aria-label={description}
            className={`inline-flex h-[1rem] w-[1rem] cursor-help items-center justify-center rounded-full border text-[0.68rem] font-bold leading-none ${meta.sigmaClassName}`}
        >
            Σ
        </span>
    );
};

export default EfficiencyRankIcon;