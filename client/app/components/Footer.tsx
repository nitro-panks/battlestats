"use client";

import Link from 'next/link';
import React, { useState } from 'react';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faReddit } from '@fortawesome/free-brands-svg-icons';
import { buildPlayerPath } from '../lib/entityRoutes';
import { trackEvent } from '../lib/umami';
import StreamerSubmissionModal from './StreamerSubmissionModal';

const Footer: React.FC = () => {
    const [streamerModalOpen, setStreamerModalOpen] = useState(false);
    return (
        <footer className="mt-6 py-4 text-center text-xs text-[var(--text-secondary)]">
            <div className="space-y-2 px-4 leading-5">
                <p>
                    Battlestats v{process.env.NEXT_PUBLIC_APP_VERSION} by{' '}
                    <Link
                        href={buildPlayerPath('lil_boots', 'na')}
                        onClick={() => trackEvent('footer-lil-boots', { realm: 'na' })}
                        className="text-[var(--accent-mid)] underline-offset-2 hover:text-[var(--accent-dark)] hover:underline"
                    >
                        lil_boots
                    </Link>
                    {' '}
                    <a
                        href="https://www.reddit.com/user/_lil_boots/"
                        onClick={() => trackEvent('outbound-link', { target: 'reddit' })}
                        className="text-[var(--accent-mid)] hover:text-[var(--accent-dark)]"
                        target="_blank"
                        rel="noreferrer"
                        title="lil_boots on Reddit"
                        aria-label="lil_boots on Reddit"
                    >
                        <FontAwesomeIcon icon={faReddit} aria-hidden="true" />
                    </a>
                    {' · '}
                    <a
                        href="https://creativecommons.org/licenses/by-nc-sa/4.0/"
                        onClick={() => trackEvent('outbound-link', { target: 'cc-license' })}
                        className="text-[var(--accent-mid)] underline-offset-2 hover:text-[var(--accent-dark)] hover:underline"
                        target="_blank"
                        rel="noreferrer"
                    >
                        CC BY-NC-SA 4.0
                    </a>
                    {' · '}
                    <a
                        href="https://github.com/nitro-panks/battlestats"
                        onClick={() => trackEvent('outbound-link', { target: 'github' })}
                        className="text-[var(--accent-mid)] underline-offset-2 hover:text-[var(--accent-dark)] hover:underline"
                        target="_blank"
                        rel="noreferrer"
                    >
                        Fork me on GitHub
                    </a>
                    {' · '}
                    <button
                        type="button"
                        onClick={() => { trackEvent('streamer-open'); setStreamerModalOpen(true); }}
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
                    Ship parameter links are provided via{' '}
                    <a
                        href="https://shiptool.st/"
                        onClick={() => trackEvent('outbound-link', { target: 'shiptool' })}
                        className="text-[var(--accent-mid)] underline-offset-2 hover:text-[var(--accent-dark)] hover:underline"
                        target="_blank"
                        rel="noreferrer"
                    >
                        Ship Tool
                    </a>
                    {' '}(shiptool.st), an independent community project. Battlestats is not affiliated with or endorsed by Ship Tool.
                </p>
                <p>
                    <a
                        href="https://worldofwarships.com/"
                        onClick={() => trackEvent('outbound-link', { target: 'wows' })}
                        className="text-[var(--accent-mid)] underline-offset-2 hover:text-[var(--accent-dark)] hover:underline"
                        target="_blank"
                        rel="noreferrer"
                    >
                        Official World of Warships website
                    </a>
                    {' · '}
                    <a
                        href="https://wargaming.net/support/"
                        onClick={() => trackEvent('outbound-link', { target: 'wg-support' })}
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
