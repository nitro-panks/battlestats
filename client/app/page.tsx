"use client";

import React, { Suspense } from 'react';
import PlayerSearch from './components/PlayerSearch';

const Page: React.FC = () => {
  return (
    <div className="w-full bg-white">
      <div className="mx-auto w-full max-w-5xl text-black">
        <Suspense fallback={null}>
          <PlayerSearch />
        </Suspense>
      </div>
    </div>
  );
};

export default Page;
