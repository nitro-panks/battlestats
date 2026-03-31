import type { Metadata } from "next";
import { Suspense } from "react";
import Script from "next/script";
import { config } from "@fortawesome/fontawesome-svg-core";
import "@fortawesome/fontawesome-svg-core/styles.css";
import { Inter } from "next/font/google";
import HeaderSearch from "./components/HeaderSearch";
import Logo from "./components/Logo";
import Footer from "./components/Footer";
import ThemeToggle from "./components/ThemeToggle";
import RealmSelector from "./components/RealmSelector";
import { ThemeProvider } from "./context/ThemeContext";
import { RealmProvider } from "./context/RealmContext";
import { getSiteOrigin } from "./lib/siteOrigin";
import "./globals.css";

config.autoAddCss = false;

const inter = Inter({ subsets: ["latin"] });
const gaMeasurementId = process.env.NEXT_PUBLIC_GA_MEASUREMENT_ID;

export const metadata: Metadata = {
  title: "WoWs Battlestats — World of Warships Player & Clan Statistics",
  description:
    "Look up any World of Warships player or clan. Win rates, battle history, ship stats, ranked performance, efficiency rankings, and population distributions.",
  metadataBase: new URL(getSiteOrigin()),
  openGraph: {
    title: "WoWs Battlestats",
    description: "World of Warships player and clan statistics.",
    siteName: "WoWs Battlestats",
    type: "website",
  },
  twitter: {
    card: "summary",
    title: "WoWs Battlestats",
    description: "World of Warships player and clan statistics.",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: `(function(){var t=localStorage.getItem('bs-theme');if(!t)t=window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';document.documentElement.dataset.theme=t;var r=localStorage.getItem('bs-realm');if(r&&['na','eu'].indexOf(r)>=0)document.documentElement.dataset.realm=r;else document.documentElement.dataset.realm='na';})();` }} />
        <script defer src="/umami/script.js" data-website-id="27c0ee6a-f534-42d4-b49f-27bbadad9848" />
      </head>
      {gaMeasurementId ? (
        <>
          <Script
            src={`https://www.googletagmanager.com/gtag/js?id=${gaMeasurementId}`}
            strategy="afterInteractive"
          />
          <Script id="google-analytics" strategy="afterInteractive">
            {`
              window.dataLayer = window.dataLayer || [];
              function gtag(){dataLayer.push(arguments);}
              window.gtag = gtag;
              gtag('js', new Date());
              gtag('config', '${gaMeasurementId}', { send_page_view: false });
            `}
          </Script>
        </>
      ) : null}
      <body className={inter.className}>
        <ThemeProvider>
          <RealmProvider>
            <div className="mx-auto max-w-6xl px-4 md:px-6">
              <header className="flex flex-col gap-4 bg-[var(--bg-page)] py-5 pl-5 md:flex-row md:items-center md:justify-between md:py-6">
                <Logo />
                <div className="flex w-full items-center justify-end gap-3 pr-2 md:w-auto">
                  <ThemeToggle />
                  <RealmSelector />
                  <Suspense fallback={null}>
                    <HeaderSearch />
                  </Suspense>
                </div>
              </header>
              <main className="pt-6 pb-8">{children}</main>
              <Footer />
            </div>
          </RealmProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
