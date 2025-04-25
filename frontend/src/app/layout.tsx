import type { Metadata } from "next";
import { Inter } from "next/font/google";
import Script from "next/script";
import "./globals.css";
import { Toaster } from "@/components/ui/toaster";
import { Providers } from './providers';
import { SidebarLayout } from "@/components/sidebar-layout";
import { getCachedConfig } from "@/utils/config";

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
        <link rel="preconnect" href={NEXT_PUBLIC_API_URL} crossOrigin="use-credentials" />
        <Script id="feedbucket" strategy="afterInteractive">
          {`
            (function(k,s) {
                s=document.createElement('script');s.module=true;s.async=true;
                s.src="https://cdn.feedbucket.app/assets/feedbucket.js";
                s.dataset.feedbucket=k;document.head.appendChild(s);
            })('3bGZapSdjMzCKHXnZ3By')
          `}
        </Script>
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
