import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { Toaster } from "@/components/ui/toaster";
import { Providers } from './providers';
import { SidebarLayout } from "@/components/sidebar-layout";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "AmazeeAI",
  description: "AI-powered development platform",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className={inter.className}>
        <Providers>
          <SidebarLayout>{children}</SidebarLayout>
          <Toaster />
        </Providers>
      </body>
    </html>
  );
}
