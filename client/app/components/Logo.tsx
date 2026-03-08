"use client";

import Link from "next/link";
import React from "react";

const Logo: React.FC = () => {
    const handleClick = (e: React.MouseEvent<HTMLAnchorElement>) => {
        e.preventDefault();
        window.dispatchEvent(new CustomEvent("resetApp"));
    };

    return (
        <Link
            href="/"
            onClick={handleClick}
            className="text-xl font-bold tracking-tight text-[#084594] hover:text-[#2171b5] transition-colors"
        >
            WoWs Battlestats
        </Link>
    );
};

export default Logo;
