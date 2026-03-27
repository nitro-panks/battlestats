"use client";

import React, { Suspense } from 'react';
import PlayerSearch from './components/PlayerSearch';

const Page: React.FC = () => {
  return (
    <div className="w-full bg-[var(--bg-page)]">
      <div className="mx-auto w-full max-w-5xl text-[var(--text-primary)]">
        <Suspense fallback={null}>
          <PlayerSearch />
        </Suspense>
      </div>
    </div>
  );
};

export default Page;
