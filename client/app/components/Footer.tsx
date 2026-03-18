"use client";

import Link from 'next/link';
import React from 'react';
import { buildPlayerPath } from '../lib/entityRoutes';

const Footer: React.FC = () => {
    return (
        <footer className="border-t border-[#c6dbef] py-4 text-center text-xs text-[#9ecae1]">
            <p>
                created by{' '}
                <Link href={buildPlayerPath('lil_boots')} className="text-[#2171b5] underline-offset-2 hover:text-[#084594] hover:underline">
                    lil_boots
                </Link>
            </p>
        </footer>
    );
};

export default Footer;
