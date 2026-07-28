import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "KnowledgeOS — grounded answers from your documents",
  description:
    "Enterprise retrieval-augmented knowledge platform. Hybrid search, verified citations, and a visible refusal threshold.",
};

export const viewport: Viewport = {
  themeColor: "#070a10",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
