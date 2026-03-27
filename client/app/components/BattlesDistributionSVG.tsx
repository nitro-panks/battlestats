import React from 'react';
import PopulationDistributionSVG from './PopulationDistributionSVG';
import { ChartTheme } from '../lib/chartTheme';

interface BattlesDistributionSVGProps {
    playerBattles: number;
    svgWidth?: number;
    svgHeight?: number;
    theme?: ChartTheme;
}

const BattlesDistributionSVG: React.FC<BattlesDistributionSVGProps> = ({
    playerBattles,
    svgWidth = 600,
    svgHeight = 184,
    theme,
}) => {
    return (
        <PopulationDistributionSVG
            primaryMetric="battles_played"
            primaryValue={playerBattles}
            svgWidth={svgWidth}
            svgHeight={svgHeight}
            theme={theme}
        />
    );
};

export default BattlesDistributionSVG;