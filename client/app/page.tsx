import React, { Suspense } from 'react';
import type { Metadata } from 'next';
import PlayerSearch from './components/PlayerSearch';
import { getSiteUrl } from './lib/siteOrigin';

export const metadata: Metadata = {
  title: 'WoWs Battlestats — World of Warships Player & Clan Statistics',
  description:
    'Look up any World of Warships player or clan. Win rates, battle history, ship stats, ranked performance, efficiency rankings, and population distributions.',
  alternates: { canonical: getSiteUrl('/') },
  openGraph: {
    title: 'WoWs Battlestats',
    description: 'World of Warships player and clan statistics.',
    url: getSiteUrl('/'),
    siteName: 'WoWs Battlestats',
    type: 'website',
  },
  twitter: {
    card: 'summary',
    title: 'WoWs Battlestats',
    description: 'World of Warships player and clan statistics.',
  },
};

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
