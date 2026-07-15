import '@testing-library/jest-dom';

// jsdom doesn't implement ResizeObserver, which d3-backed chart/landing
// components rely on. Provide a no-op so those components mount under Jest
// (e.g. PlayerSearch.test.tsx, which renders the landing surfaces).
if (typeof globalThis.ResizeObserver === 'undefined') {
    globalThis.ResizeObserver = class {
        observe() {}
        unobserve() {}
        disconnect() {}
    } as unknown as typeof ResizeObserver;
}

// jsdom doesn't implement SVG text measurement. The D3 charts call getBBox()
// for label layout, which throws "not implemented" and kicks the chart into
// its error state under Jest. A fixed-size stub keeps layout math finite.
const svgPrototype = typeof SVGElement !== 'undefined'
    ? (SVGElement.prototype as unknown as { getBBox?: () => { x: number; y: number; width: number; height: number } })
    : null;
if (svgPrototype && !svgPrototype.getBBox) {
    svgPrototype.getBBox = () => ({ x: 0, y: 0, width: 24, height: 12 });
}