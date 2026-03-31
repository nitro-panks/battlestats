import React, { useEffect, useState, useRef } from 'react';
import * as d3 from 'd3';
import { chartColors, type ChartTheme } from '../lib/chartTheme';
import { useRealm } from '../context/RealmContext';
import { withRealm } from '../lib/realmParams';

interface ActivityProps {
    playerId: number;
    theme?: ChartTheme;
}

interface ActivityRow {
    date: string;
    battles: number;
    wins: number;
}

const ActivitySVG: React.FC<ActivityProps> = ({ playerId, theme = 'light' }) => {
    const [isAllZeroWindow, setIsAllZeroWindow] = useState(false);
    const [chartData, setChartData] = useState<{ rows: ActivityRow[], error: boolean } | null>(null);
    const containerRef = useRef<HTMLDivElement>(null);
    const { realm } = useRealm();

    // 1. Fetch data ONCE whenever playerId changes
    useEffect(() => {
        let isMounted = true;
        const path = withRealm(`/api/fetch/activity_data/${playerId}/`, realm);

        fetch(path)
            .then(response => response.json())
            .then(data => {
                if (!isMounted) return;
                // API now returns a flat list; legacy wrapper is also handled
                const rows: ActivityRow[] = Array.isArray(data)
                    ? data
                    : (data?.data || data?.mode_data?.pvp || []) as ActivityRow[];

                setIsAllZeroWindow(rows.length > 0 && rows.every((row) => (row.battles || 0) === 0));
                setChartData({ rows, error: false });
            })
            .catch(() => {
                if (!isMounted) return;
                setIsAllZeroWindow(false);
                setChartData({ rows: [], error: true });
            });

        return () => {
            isMounted = false;
        };
    }, [playerId, realm]);

    // 2. Safely render D3 chart ONLY after data is available and state is settled
    useEffect(() => {
        if (!containerRef.current || !chartData) return;

        const colors = chartColors[theme];
        const container = containerRef.current;
        d3.select(container).selectAll("*").remove(); // Clean up safely

        const { rows, error } = chartData;

        const margin = { top: 20, right: 20, bottom: 50, left: 70 };
        const width = 500 - margin.left - margin.right;
        const height = 230 - margin.top - margin.bottom;

        const svg = d3.select(container)
            .append("svg")
            .attr("width", width + margin.left + margin.right)
            .attr("height", height + margin.top + margin.bottom)
            .append("g")
            .attr("transform", `translate(${margin.left}, ${margin.top})`);

        try {
            if (error) {
                svg.append("text")
                    .attr("x", 0)
                    .attr("y", 16)
                    .style("font-size", "12px")
                    .style("fill", colors.labelText)
                    .text("Unable to load activity data.");
                return;
            }

            if (!rows || rows.length === 0) {
                svg.append("text")
                    .attr("x", 0)
                    .attr("y", 16)
                    .style("font-size", "12px")
                    .style("fill", colors.labelText)
                    .text("No recent activity data available.");
                return;
            }

            const startDate = new Date(Date.now() - (28 * 24 * 60 * 60 * 1000));
            // Ensure array behaves properly and values are valid safely:
            let maxBattles = d3.max(rows, (d: ActivityRow) => {
                const b = parseInt(String(d.battles || 0), 10);
                return isNaN(b) ? 0 : b;
            }) || 0;
            maxBattles = Math.max(maxBattles, 2) + 1;

            const x = d3.scaleTime()
                .domain([startDate, new Date()])
                .range([6, width]);

            svg.append("g")
                .attr("transform", `translate(0, ${height})`)
                .call(d3.axisBottom(x).ticks(8))
                .selectAll("text")
                .attr("transform", "translate(-10,0)rotate(-45)")
                .style("text-anchor", "end");

            const y = d3.scaleLinear()
                .domain([maxBattles, 0])
                .range([1, height]);

            svg.append("g")
                .call(d3.axisLeft(y).ticks(5));

            svg.append("text")
                .attr("x", width)
                .attr("y", height + 44)
                .attr("text-anchor", "end")
                .style("font-size", "10px")
                .style("fill", colors.labelText)
                .text("Date");

            svg.append("text")
                .attr("transform", "rotate(-90)")
                .attr("x", -4)
                .attr("y", -52)
                .attr("text-anchor", "end")
                .style("font-size", "10px")
                .style("fill", colors.labelText)
                .text("Battles");

            svg.append("text")
                .attr("x", width)
                .attr("y", 12)
                .attr("text-anchor", "end")
                .style("font-size", "10px")
                .style("fill", colors.labelText)
                .text("Gray = total games, Green = wins");

            const nodes = svg.selectAll(".rect")
                .data(rows)
                .enter()
                .append("g")
                .classed('rect', true);

            nodes.append("rect")
                .attr("x", (d: ActivityRow) => {
                    const parsedDate = new Date(d.date);
                    return isNaN(parsedDate.getTime()) ? 0 : x(parsedDate);
                })
                .attr("y", (d: ActivityRow) => {
                    const val = parseInt(String(d.battles || 0), 10);
                    return y(isNaN(val) ? 0 : val);
                })
                .attr("height", (d: ActivityRow) => {
                    const val = parseInt(String(d.battles || 0), 10);
                    const yVal = y(isNaN(val) ? 0 : val);
                    return height - yVal;
                })
                .attr("width", "12")
                .attr("fill", colors.axisLine)
                .on('mouseover', function (event: any, d: ActivityRow) {
                    showRecentDetails(d);
                })
                .on('mouseout', function (event: any, d: ActivityRow) {
                    hideRecentDetails();
                });

            nodes.append("rect")
                .attr("x", (d: ActivityRow) => {
                    const parsedDate = new Date(d.date);
                    return (isNaN(parsedDate.getTime()) ? 0 : x(parsedDate)) + 1;
                })
                .attr("y", (d: ActivityRow) => {
                    const val = parseInt(String(d.wins || 0), 10);
                    return y(isNaN(val) ? 0 : val);
                })
                .attr("height", (d: ActivityRow) => {
                    const val = parseInt(String(d.wins || 0), 10);
                    const yVal = y(isNaN(val) ? 0 : val);
                    return height - yVal;
                })
                .attr("width", "10")
                .style("stroke", colors.axisLine)
                .style("stroke-width", 0.5)
                .attr("fill", colors.wrVeryGood)
                .on('mouseover', function (this: any, event: any, d: ActivityRow) {
                    showRecentDetails(d);
                    d3.select(this).transition()
                        .duration(50)
                        .attr('fill', colors.surface);
                })
                .on('mouseout', function (this: any, event: any, d: ActivityRow) {
                    hideRecentDetails();
                    d3.select(this).transition()
                        .duration(50)
                        .attr("fill", colors.wrVeryGood);
                });

            const showRecentDetails = (d: ActivityRow) => {
                const startX = 470, startY = 30;

                const detailGroup = d3.select(containerRef.current).select("svg").append("g")
                    .classed('details', true);

                detailGroup.append("text")
                    .attr("x", startX)
                    .attr("y", startY)
                    .style("font-size", "12px")
                    .style("font-weight", "700")
                    .attr("text-anchor", "end")
                    .text(d.date || "Unknown");

                detailGroup.append("text")
                    .attr("x", startX + 110)
                    .attr("y", startY)
                    .style("font-size", "12px")
                    .attr("text-anchor", "end")
                    .text(`${d.wins || 0} W : ${d.battles || 0} Games`);
            };

            const hideRecentDetails = () => {
                const detailGroup = d3.select(containerRef.current).select(".details");
                detailGroup.remove();
            };
        } catch (e: any) {
            console.error("Activity SVG Render Error:", e);
            svg.append("text")
                .attr("x", 0)
                .attr("y", 16)
                .style("font-size", "12px")
                .style("fill", "red")
                .text("Error rendering chart: " + e.message);
        }
    }, [chartData, theme]); // Watch chartData and theme

    return (
        <div>
            {isAllZeroWindow ? (
                <p className="mb-2 text-xs text-[var(--text-secondary)]">
                    No daily Random Battles activity recorded in the last 28 days.
                </p>
            ) : null}
            <div ref={containerRef}></div>
        </div>
    );
};

export default ActivitySVG;
