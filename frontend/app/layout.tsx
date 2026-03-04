import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Poke Scan",
  description: "Local-first Pokemon card scanner demo",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
