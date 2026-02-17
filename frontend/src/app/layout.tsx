import type { Metadata } from "next";
import { Inter } from "next/font/google";
import { SidebarLayout } from "@/components/sidebar-layout";
import { Toaster } from "@/components/ui/toaster";
import { getCachedConfig } from "@/utils/config";
import "./globals.css";
import { Providers } from "./providers";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "amazee.ai",
  description: "AI-powered development platform",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const { NEXT_PUBLIC_API_URL } = getCachedConfig();

  return (
    <html lang="en">
      <head>
        <link rel="dns-prefetch" href={NEXT_PUBLIC_API_URL} />
        <link
          rel="preconnect"
          href={NEXT_PUBLIC_API_URL}
          crossOrigin="use-credentials"
        />
      </head>
      <body className={inter.className}>
        <Providers>
          <SidebarLayout>{children}</SidebarLayout>
          <Toaster />
        </Providers>
      </body>
    </html>
  );
}
