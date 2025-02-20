'use client';

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useState, useEffect } from 'react';
import { getConfig } from '@/utils/config';
import { useAuth } from '@/hooks/use-auth';
import { get } from '@/utils/api';

export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(() => new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 5 * 60 * 1000, // 5 minutes
        retry: 1,
      },
    },
  }));
  const { setUser } = useAuth();

  useEffect(() => {
    // Load configuration when the app starts
    getConfig().catch(console.error);

    // Try to fetch user profile if session exists
    const initializeUser = async () => {
      try {
        const response = await get('/auth/me');
        if (response.ok) {
          const userData = await response.json();
          setUser(userData);
        }
      } catch (error) {
        console.error('Failed to fetch user profile:', error);
        // Don't set any error state, just let the user stay logged out
      }
    };

    initializeUser();
  }, [setUser]);

  return (
    <QueryClientProvider client={queryClient}>
      {children}
    </QueryClientProvider>
  );
}