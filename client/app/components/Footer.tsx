"use client";

import Link from 'next/link';
import React, { useState } from 'react';
import { buildPlayerPath } from '../lib/entityRoutes';
import StreamerSubmissionModal from './StreamerSubmissionModal';

const Footer: React.FC = () => {
    const [streamerModalOpen, setStreamerModalOpen] = useState(false);
    return (
        <footer className="border-t border-[var(--border)] py-4 text-center text-xs text-[var(--text-secondary)]">
            <div className="space-y-2 px-4 leading-5">
                <p>
                    Battlestats v{process.env.NEXT_PUBLIC_APP_VERSION} by{' '}
                    <Link href={buildPlayerPath('lil_boots', 'na')} className="text-[var(--accent-mid)] underline-offset-2 hover:text-[var(--accent-dark)] hover:underline">
                        lil_boots
                    </Link>
                    {' · '}
                    <a
                        href="https://creativecommons.org/licenses/by-nc-sa/4.0/"
                        className="text-[var(--accent-mid)] underline-offset-2 hover:text-[var(--accent-dark)] hover:underline"
                        target="_blank"
                        rel="noreferrer"
                    >
                        CC BY-NC-SA 4.0
                    </a>
                    {' · '}
                    <a
                        href="https://github.com/nitro-panks/battlestats"
                        className="text-[var(--accent-mid)] underline-offset-2 hover:text-[var(--accent-dark)] hover:underline"
                        target="_blank"
                        rel="noreferrer"
                    >
                        Fork me on GitHub
                    </a>
                    {' · '}
                    <button
                        type="button"
                        onClick={() => setStreamerModalOpen(true)}
                        className="text-[var(--accent-mid)] underline-offset-2 hover:text-[var(--accent-dark)] hover:underline"
                    >
                        Add a streamer!
                    </button>
                </p>
                <p>Data sourced from the Wargaming API. Not affiliated with Wargaming.net.</p>
                <p>
                    World of Warships data is sourced from the official Wargaming API. Battlestats is an independent fan project and is not affiliated with, endorsed by, or sponsored by Wargaming.
                </p>
                <p>
                    <a
                        href="https://worldofwarships.com/"
                        className="text-[var(--accent-mid)] underline-offset-2 hover:text-[var(--accent-dark)] hover:underline"
                        target="_blank"
                        rel="noreferrer"
                    >
                        Official World of Warships website
                    </a>
                    {' · '}
                    <a
                        href="https://www.support.wargaming.net/"
                        className="text-[var(--accent-mid)] underline-offset-2 hover:text-[var(--accent-dark)] hover:underline"
                        target="_blank"
                        rel="noreferrer"
                    >
                        Wargaming Player Support
                    </a>
                </p>
            </div>
            <StreamerSubmissionModal
                open={streamerModalOpen}
                onClose={() => setStreamerModalOpen(false)}
            />
        </footer>
    );
};

export default Footer;
