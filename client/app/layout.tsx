import type { Metadata } from "next";
import { Suspense } from "react";
import Script from "next/script";
import { config } from "@fortawesome/fontawesome-svg-core";
import "@fortawesome/fontawesome-svg-core/styles.css";
import { Inter } from "next/font/google";
import HeaderSearch from "./components/HeaderSearch";
import Logo from "./components/Logo";
import Footer from "./components/Footer";
import "./globals.css";

config.autoAddCss = false;

const inter = Inter({ subsets: ["latin"] });
const gaMeasurementId = process.env.NEXT_PUBLIC_GA_MEASUREMENT_ID;

export const metadata: Metadata = {
  title: "WoWs Battlestats",
  description: "World of Warships player and clan statistics",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
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
        <div className="mx-auto max-w-6xl px-4 md:px-6">
          <header className="flex flex-col gap-4 bg-white py-5 pl-5 md:flex-row md:items-center md:justify-between md:py-6">
            <Logo />
            <div className="flex w-full justify-end pr-2 md:w-auto">
              <Suspense fallback={null}>
                <HeaderSearch />
              </Suspense>
            </div>
          </header>
          <main className="pt-6 pb-8">{children}</main>
          <Footer />
        </div>
      </body>
    </html>
  );
}
