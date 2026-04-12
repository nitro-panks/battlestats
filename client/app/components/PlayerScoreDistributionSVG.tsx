import React from 'react';
import PopulationDistributionSVG from './PopulationDistributionSVG';
import { ChartTheme } from '../lib/chartTheme';

interface PlayerScoreDistributionSVGProps {
    playerScore: number | null;
    svgWidth?: number;
    svgHeight?: number;
    theme?: ChartTheme;
}

const SCORE_FLOOR = 2.0;

const PlayerScoreDistributionSVG: React.FC<PlayerScoreDistributionSVGProps> = ({
    playerScore,
    svgWidth = 600,
    svgHeight = 184,
    theme,
}) => {
    if (playerScore == null || playerScore < SCORE_FLOOR) {
        return null;
    }

    return (
        <PopulationDistributionSVG
            primaryMetric="player_score"
            primaryValue={playerScore}
            svgWidth={svgWidth}
            svgHeight={svgHeight}
            theme={theme}
        />
    );
};

export default PlayerScoreDistributionSVG;
