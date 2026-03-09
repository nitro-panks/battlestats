import type { Metadata } from "next";
import { Inter } from "next/font/google";
import Logo from "./components/Logo";
import Footer from "./components/Footer";
import "./globals.css";

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
          <header className="flex items-center bg-white py-5 md:py-6 pl-5">
            <Logo />
          </header>
          <main className="pt-6 pb-8">{children}</main>
          <Footer />
        </div>
      </body>
    </html>
  );
}
