import type { Metadata } from "next";
import { config } from "@fortawesome/fontawesome-svg-core";
import "@fortawesome/fontawesome-svg-core/styles.css";
import Link from "next/link";
import { Inter } from "next/font/google";
import Logo from "./components/Logo";
import Footer from "./components/Footer";
import "./globals.css";

config.autoAddCss = false;

const inter = Inter({ subsets: ["latin"] });

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
      <body className={inter.className}>
        <div className="mx-auto max-w-6xl px-4 md:px-6">
          <header className="flex items-center justify-between gap-4 bg-white py-5 pl-5 md:py-6">
            <Logo />
            <nav className="flex items-center gap-4 pr-2 text-sm font-medium text-[#6baed6]">
              <Link href="/" className="transition-colors hover:text-[#084594]">
                Home
              </Link>
              <Link href="/trace" className="transition-colors hover:text-[#084594]">
                Trace
              </Link>
            </nav>
          </header>
          <main className="pt-6 pb-8">{children}</main>
          <Footer />
        </div>
      </body>
    </html>
  );
}
