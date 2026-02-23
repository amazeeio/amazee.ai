"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/hooks/use-auth";
import { get, setOnUnauthorized } from "@/utils/api";
import { getConfig } from "@/utils/config";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 5 * 60 * 1000, // 5 minutes
            retry: 1,
          },
        },
      }),
  );
  const { setUser } = useAuth();
  const router = useRouter();

  useEffect(() => {
    setOnUnauthorized(() => {
      setUser(null);
      const currentPath = window.location.pathname;
      router.push(`/auth/login?from=${encodeURIComponent(currentPath)}`);
    });
  }, [setUser, router]);

  useEffect(() => {
    // Load configuration when the app starts
    getConfig().catch(console.error);

    // Try to fetch user profile if session exists
    const initializeUser = async () => {
      try {
        const response = await get("/auth/me");
        if (response.ok) {
          const userData = await response.json();
          setUser(userData);
        }
      } catch (error) {
        console.error("Failed to fetch user profile:", error);
        // Don't set any error state, just let the user stay logged out
      }
    };

    initializeUser();
  }, [setUser]);

  return (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}
