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
    console.log('loadConfig called');
    const state = get();
    console.log('current state loading:', state.loading, 'isLoaded:', state.isLoaded);
    
    if (state.isLoaded && state.config) {
      console.log('loadConfig: already loaded successfully, returning cached config');
      return state.config; // Return cached config if already loaded successfully
    }
    
    if (state.loading) {
      console.log('loadConfig: already loading, returning early');
      return null; // Return null if already loading
    }
    
    console.log('loadConfig: setting loading to true');
    set({ loading: true, error: null });
    
    try {
      console.log('loadConfig: about to fetch /api/config');
      const response = await fetch('/api/config');
      console.log('config response', response);
      if (!response.ok) {
        throw new Error('Failed to load configuration');
      }
      
      const config: Config = await response.json();
      console.log('parsed', config);
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

// Config is loaded on-demand when needed, not automatically on window init
