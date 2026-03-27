import React from 'react';
import WRDistributionDesign1SVG from './WRDistributionDesign1SVG';
import WRDistributionDesign2SVG from './WRDistributionDesign2SVG';
import { ChartTheme } from '../lib/chartTheme';

interface WRDistributionProps {
    playerWR: number;
    playerSurvivalRate?: number | null;
    svgWidth?: number;
    svgHeight?: number;
    theme?: ChartTheme;
}
const WRDistributionSVG: React.FC<WRDistributionProps> = ({
    playerWR,
    playerSurvivalRate = null,
    svgWidth = 600,
    svgHeight = 248,
    theme,
}) => {
    return (
        <WRDistributionDesign2SVG
            playerWR={playerWR}
            playerSurvivalRate={playerSurvivalRate}
            svgWidth={svgWidth}
            svgHeight={svgHeight}
            theme={theme}
        />
    );
};

export { WRDistributionDesign1SVG, WRDistributionDesign2SVG };
export default WRDistributionSVG;
