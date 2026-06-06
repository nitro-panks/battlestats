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