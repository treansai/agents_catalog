import type { Metadata, Viewport } from "next";
import { IBM_Plex_Mono, Instrument_Sans } from "next/font/google";
import type { ReactNode } from "react";

import "maplibre-gl/dist/maplibre-gl.css";

import "./globals.css";
import "./agent-ui.css";

// Polices de la console vocale, auto-hébergées. Le tableau de bord garde ses piles système.
const instrumentSans = Instrument_Sans({
  subsets: ["latin"],
  variable: "--font-voice",
  display: "swap",
});

const ibmPlexMono = IBM_Plex_Mono({
  weight: ["400", "500"],
  subsets: ["latin"],
  variable: "--font-voice-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Ezer — L’essentiel de votre boîte mail",
  description:
    "Ezer analyse vos emails en lecture seule et fait remonter les priorités, les actions et les risques.",
  applicationName: "Ezer",
};

export const viewport: Viewport = {
  themeColor: "#eaf2f5",
  colorScheme: "light",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html className={`${instrumentSans.variable} ${ibmPlexMono.variable}`} lang="fr">
      <body>{children}</body>
    </html>
  );
}
