import { RequestQueue } from '../requestQueue';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('RequestQueue', () => {
    it('runs up to the cap concurrently and queues the rest', async () => {
        const q = new RequestQueue(2);
        const r1 = await q.acquire('high');
        const r2 = await q.acquire('high');
        expect(q.getActive()).toBe(2);

        let third = false;
        const p3 = q.acquire('high').then((release) => { third = true; return release; });
        await flush();
        expect(third).toBe(false); // capped
        expect(q.getQueued()).toBe(1);

        r1(); // free a slot
        const r3 = await p3;
        expect(third).toBe(true);

        r2();
        r3();
        expect(q.getActive()).toBe(0);
    });

    it('grants queued slots in priority order (critical before high before low)', async () => {
        const q = new RequestQueue(1);
        const r1 = await q.acquire('high'); // occupies the only slot
        const order: string[] = [];

        const pLow = q.acquire('low').then((r) => { order.push('low'); return r; });
        const pCritical = q.acquire('critical').then((r) => { order.push('critical'); return r; });
        const pHigh = q.acquire('high').then((r) => { order.push('high'); return r; });
        await flush();

        // Release one at a time; each release should admit the highest priority waiter.
        r1();
        (await pCritical)();
        (await pHigh)();
        (await pLow)();

        expect(order).toEqual(['critical', 'high', 'low']);
    });

    it('removes a queued waiter when its signal aborts (never runs)', async () => {
        const q = new RequestQueue(1);
        const r1 = await q.acquire('high'); // occupy
        const controller = new AbortController();
        const pending = q.acquire('high', controller.signal);

        await flush();
        expect(q.getQueued()).toBe(1);

        controller.abort();
        await expect(pending).rejects.toHaveProperty('name', 'AbortError');
        expect(q.getQueued()).toBe(0);

        r1();
        expect(q.getActive()).toBe(0);
    });

    it('rejects immediately if the signal is already aborted', async () => {
        const q = new RequestQueue(5);
        const controller = new AbortController();
        controller.abort();
        await expect(q.acquire('high', controller.signal)).rejects.toHaveProperty('name', 'AbortError');
        expect(q.getActive()).toBe(0);
    });

    it('drains newly-fitting waiters when the cap is raised', async () => {
        const q = new RequestQueue(1);
        const r1 = await q.acquire('high');
        let granted = false;
        const p2 = q.acquire('high').then((r) => { granted = true; return r; });
        await flush();
        expect(granted).toBe(false);

        q.setCap(2); // now two fit
        await p2;
        expect(granted).toBe(true);
        r1();
    });
});
