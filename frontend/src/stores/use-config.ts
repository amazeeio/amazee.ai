'use client';

import { create } from 'zustand';

export interface Config {
  NEXT_PUBLIC_API_URL: string;
  PASSWORDLESS_SIGN_IN: boolean;
  STRIPE_PUBLISHABLE_KEY: string;
}

interface ConfigState {
  config: Config | null;
  loading: boolean;
  error: string | null;
  setConfig: (config: Config) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
  loadConfig: () => Promise<void>;
  getApiUrl: () => string;
}

const fallbackConfig: Config = {
  NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8800',
  PASSWORDLESS_SIGN_IN: process.env.PASSWORDLESS_SIGN_IN === 'true',
  STRIPE_PUBLISHABLE_KEY: process.env.STRIPE_PUBLISHABLE_KEY || '',
};

export const useConfig = create<ConfigState>((set, get) => ({
  config: fallbackConfig,
  loading: false,
  error: null,
  
  setConfig: (config) => set({ config, error: null }),
  setLoading: (loading) => set({ loading }),
  setError: (error) => set({ error, loading: false }),
  
  loadConfig: async () => {
    const state = get();
    if (state.loading) return; // Prevent multiple simultaneous requests
    
    set({ loading: true, error: null });
    
    try {
      const response = await fetch('/api/config');
      console.log(response);
      if (!response.ok) {
        throw new Error('Failed to load configuration');
      }
      
      const config: Config = await response.json();
      console.log(config);
      set({ config, loading: false, error: null });
    } catch (error) {
      console.error('Error loading configuration:', error);
      console.log('error', fallbackConfig);
      set({ 
        config: fallbackConfig, 
        loading: false, 
        error: error instanceof Error ? error.message : 'Failed to load configuration'
      });
    }
  },
  
  getApiUrl: () => {
    const state = get();
    return state.config?.NEXT_PUBLIC_API_URL || fallbackConfig.NEXT_PUBLIC_API_URL;
  },
}));

// Initialize config on store creation (only in browser environment)
if (typeof window !== 'undefined') {
  useConfig.getState().loadConfig();
}
