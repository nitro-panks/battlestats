"use client";

import React, { useEffect, useState } from 'react';

interface DbStats {
    players: number;
    clans: number;
}

const Footer: React.FC = () => {
    const [stats, setStats] = useState<DbStats | null>(null);

    useEffect(() => {
        fetch('http://localhost:8888/api/stats/')
            .then(res => {
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                return res.json();
            })
            .then((data: DbStats) => setStats(data))
            .catch(() => {});
    }, []);

    return (
        <footer className="border-t border-[#c6dbef] py-4 text-center text-xs text-[#9ecae1]">
            {stats && (
                <p>
                    {stats.players.toLocaleString()} players &middot; {stats.clans.toLocaleString()} clans
                </p>
            )}
            <p className="mt-1" style={{ fontSize: '10px' }}>
                This is purely informational for personal understanding.
            </p>
        </footer>
    );
};

export default Footer;
