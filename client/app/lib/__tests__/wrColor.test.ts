import wrColor from '../wrColor';

// wrColor is the site-wide win-rate → color contract (17 importers). These
// tests pin every band boundary so a threshold typo can't silently mis-color
// every WR figure on the site.
describe('wrColor', () => {
    it('maps null to the neutral fallback', () => {
        expect(wrColor(null)).toBe('#c6dbef');
    });

    it.each([
        [65.1, '#810c9e'], // > 65 — elite
        [65, '#D042F3'],   // boundary: 65 itself is NOT elite
        [60, '#D042F3'],   // >= 60
        [59.9, '#3182bd'],
        [56, '#3182bd'],   // >= 56
        [55.9, '#74c476'],
        [54, '#74c476'],   // >= 54
        [53.9, '#a1d99b'],
        [52, '#a1d99b'],   // >= 52
        [51.9, '#fed976'],
        [50, '#fed976'],   // >= 50
        [49.9, '#fd8d3c'],
        [45, '#fd8d3c'],   // >= 45
        [44.9, '#a50f15'],
        [0, '#a50f15'],
    ])('maps %s%% to %s', (winRate, expected) => {
        expect(wrColor(winRate)).toBe(expected);
    });
});
