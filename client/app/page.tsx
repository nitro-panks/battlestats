"use client";

import React, { Suspense } from 'react';
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
      <div className="mx-auto w-full max-w-5xl text-[var(--text-primary)]">
        <Suspense fallback={null}>
          <PlayerSearch />
        </Suspense>
      </div>
    </div>
  );
}
