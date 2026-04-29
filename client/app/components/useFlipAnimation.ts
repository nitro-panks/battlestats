import { useLayoutEffect, useRef } from 'react';

/**
 * FLIP-style enter/move animation for keyed list items.
 *
 * Pass each rendered item's DOM element to `register(key, el)` (typically via
 * a callback ref). On every render, the hook compares each element's current
 * position against its previous-render position; for items that moved
 * **upward** (delta y < 0), it applies an inverted transform so the item
 * paints at its old position, then in the next frame removes the transform
 * with a CSS transition. The browser plays the slide-up.
 *
 * Items that move down or sideways are not animated — the change there is
 * usually "someone else moved up past me," which is communicated by the
 * upward animation already.
 *
 * Items that moved up also briefly receive the `flip-flash` class so the
 * caller can attach a CSS pulse / highlight ring. The class auto-clears
 * after the animation duration.
 */
const SLIDE_DURATION_MS = 360;
const FLASH_CLASS = 'flip-flash';

export interface FlipAnimationApi {
    register: (key: string, el: HTMLElement | null) => void;
}

export const useFlipAnimation = (
    keys: ReadonlyArray<string>,
): FlipAnimationApi => {
    const elementsRef = useRef<Map<string, HTMLElement>>(new Map());
    const previousRectsRef = useRef<Map<string, DOMRect>>(new Map());
    const keysSignature = keys.join('|');

    const register = (key: string, el: HTMLElement | null) => {
        if (el) {
            elementsRef.current.set(key, el);
        } else {
            elementsRef.current.delete(key);
        }
    };

    useLayoutEffect(() => {
        const elements = elementsRef.current;
        const previousRects = previousRectsRef.current;
        const nextRects = new Map<string, DOMRect>();

        elements.forEach((el, key) => {
            const rect = el.getBoundingClientRect();
            nextRects.set(key, rect);

            const previous = previousRects.get(key);
            if (!previous) return;

            const deltaY = previous.top - rect.top;
            if (deltaY <= 0) return;

            // FLIP: paint at old spot, then transition to identity.
            el.style.transition = 'none';
            el.style.transform = `translateY(${-deltaY}px)`;
            el.classList.add(FLASH_CLASS);

            requestAnimationFrame(() => {
                requestAnimationFrame(() => {
                    el.style.transition = `transform ${SLIDE_DURATION_MS}ms cubic-bezier(0.22, 1, 0.36, 1)`;
                    el.style.transform = '';
                });
            });

            window.setTimeout(() => {
                if (!el.isConnected) return;
                el.classList.remove(FLASH_CLASS);
                el.style.transition = '';
            }, SLIDE_DURATION_MS + 80);
        });

        previousRectsRef.current = nextRects;
    }, [keysSignature]);

    return { register };
};

export default useFlipAnimation;
