import type { Metadata } from "next";
import { Fraunces, Geist } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const fraunces = Fraunces({ variable: "--font-fraunces", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Kin",
  description: "A voice companion for older adults who live alone, and a window for their family.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${geistSans.variable} ${fraunces.variable} h-full antialiased`}>
      <body className="min-h-full flex flex-col font-sans">
        <header className="border-b border-line">
          <nav className="mx-auto flex max-w-5xl items-center gap-6 px-4 py-4 sm:px-6">
            <Link href="/" className="font-display text-2xl font-semibold tracking-tight">
              Kin
            </Link>
            <div className="ml-auto flex gap-1 text-sm">
              <Link href="/" className="rounded-full px-3 py-1.5 text-ink-2 hover:bg-accent-soft hover:text-ink">
                Family
              </Link>
              <Link href="/talk" className="rounded-full px-3 py-1.5 text-ink-2 hover:bg-accent-soft hover:text-ink">
                Talk to Kin
              </Link>
            </div>
          </nav>
        </header>
        <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-8 sm:px-6">{children}</main>
        <footer className="mx-auto w-full max-w-5xl px-4 pb-8 text-xs text-ink-3 sm:px-6">
          Kin is not a medical device and does not give medical advice.
        </footer>
      </body>
    </html>
  );
}
