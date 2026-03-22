"use client";

import Link from 'next/link';
import React from 'react';
import { buildPlayerPath } from '../lib/entityRoutes';

const Footer: React.FC = () => {
    return (
        <footer className="border-t border-[#c6dbef] py-4 text-center text-xs text-[#6b7280]">
            <div className="space-y-2 px-4 leading-5">
                <p>
                    Battlestats copyright 2026 by{' '}
                    <Link href={buildPlayerPath('lil_boots')} className="text-[#2171b5] underline-offset-2 hover:text-[#084594] hover:underline">
                        lil_boots
                    </Link>
                </p>
                <p>© Wargaming.net. All rights reserved.</p>
                <p>
                    World of Warships data is sourced from the official Wargaming API. Battlestats is an independent fan project and is not affiliated with, endorsed by, or sponsored by Wargaming.
                </p>
                <p>
                    <a
                        href="https://worldofwarships.com/"
                        className="text-[#2171b5] underline-offset-2 hover:text-[#084594] hover:underline"
                        target="_blank"
                        rel="noreferrer"
                    >
                        Official World of Warships website
                    </a>
                    {' · '}
                    <a
                        href="https://www.support.wargaming.net/"
                        className="text-[#2171b5] underline-offset-2 hover:text-[#084594] hover:underline"
                        target="_blank"
                        rel="noreferrer"
                    >
                        Wargaming Player Support
                    </a>
                </p>
            </div>
        </footer>
    );
};

export default Footer;
