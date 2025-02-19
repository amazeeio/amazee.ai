'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';

interface AuthState {
  isAuthenticated: boolean;
  isLoading: boolean;
  user: any | null;
}

export function useAuth() {
  const [state, setState] = useState<AuthState>({
    isAuthenticated: false,
    isLoading: true,
    user: null,
  });

  useEffect(() => {
    const checkAuth = async () => {
      try {
        console.log('Checking auth status...');
        const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/auth/me`, {
          credentials: 'include', // Important: needed to include the auth cookie
        });
        console.log('Auth response status:', response.status);

        if (response.ok) {
          const user = await response.json();
          console.log('Auth successful, user:', user);
          setState({
            isAuthenticated: true,
            isLoading: false,
            user,
          });
        } else {
          console.log('Auth failed:', await response.text());
          setState({
            isAuthenticated: false,
            isLoading: false,
            user: null,
          });
        }
      } catch (error) {
        console.error('Auth error:', error);
        setState({
          isAuthenticated: false,
          isLoading: false,
          user: null,
        });
      }
    };

    checkAuth();
  }, []);

  return state;
}