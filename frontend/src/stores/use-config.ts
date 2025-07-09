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
  isLoaded: boolean;
  setConfig: (config: Config) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
  setIsLoaded: (isLoaded: boolean) => void;
  loadConfig: () => Promise<Config | null>;
  getApiUrl: () => string;
}


export const useConfig = create<ConfigState>((set, get) => ({
  config: null,
  loading: false,
  error: null,
  isLoaded: false,
  
  setConfig: (config) => set({ config, error: null }),
  setLoading: (loading) => set({ loading }),
  setError: (error) => set({ error, loading: false }),
  setIsLoaded: (isLoaded) => set({ isLoaded }),
  
  loadConfig: async () => {
    const state = get();
    
    if (state.isLoaded && state.config) {
      return state.config; // Return cached config if already loaded successfully
    }
    
    if (state.loading) {
      return null; // Return null if already loading
    }
    
    set({ loading: true, error: null });
    
    try {
      const response = await fetch('/api/config');
      if (!response.ok) {
        throw new Error('Failed to load configuration');
      }
      
      const config: Config = await response.json();
      set({ config, loading: false, error: null, isLoaded: true });
      return config;
    } catch (error) {
      console.error('Error loading configuration:', error);
      set({ 
        config: null, 
        loading: false, 
        error: error instanceof Error ? error.message : 'Failed to load configuration',
        isLoaded: false
      });
      return null;
    }
  },
  
  getApiUrl: () => {
    const state = get();
    return state.config?.NEXT_PUBLIC_API_URL || 'http://localhost:8800';
  },
}));

