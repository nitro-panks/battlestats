"use client";

import React from 'react';
import PlayerSearch from './components/PlayerSearch';
import { getSiteUrl } from './lib/siteOrigin';

const jsonLd = {
  '@context': 'https://schema.org',
  '@type': 'WebSite',
  name: 'WoWs Battlestats',
  url: getSiteUrl('/'),
  potentialAction: {
    '@type': 'SearchAction',
    target: `${getSiteUrl('/')}?q={search_term_string}`,
    'query-input': 'required name=search_term_string',
  },
};

export default function Page() {
  return (
    <div className="w-full bg-[var(--bg-page)]">
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />
      {/* Match the player page's content bounds: on lg the content spans from the
          content-column left edge (container content + rail p-6 24px) to the
          main-well right edge (container content − 24px), the same band the
          header, footer, and player page use. */}
      <div className="w-full text-[var(--text-primary)] lg:pl-6 lg:pr-6">
        <PlayerSearch />
      </div>
    </div>
  );
}
