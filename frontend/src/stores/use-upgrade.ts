'use client';

import { create } from 'zustand';
import { getWithToken } from '@/utils/api';
import { useConfig } from './use-config';

export interface User {
  id: number;
  email: string;
  is_active: boolean;
  is_admin: boolean;
  team_id: number | null;
  role: string | null;
}

export interface PricingTable {
  pricing_table_id: string;
  updated_at: string;
}

export interface PricingTableSession {
  client_secret: string;
}

export interface StripeFormProps {
  pricingTableId: string;
  publishableKey: string;
  clientSecret: string;
}

interface Config {
  NEXT_PUBLIC_API_URL: string;
  PASSWORDLESS_SIGN_IN: boolean;
  STRIPE_PUBLISHABLE_KEY: string;
}

interface UpgradeState {
  // State
  user: User | null;
  pricingTable: PricingTable | null;
  clientSecret: string | null;
  error: string | null;
  loading: boolean;
  isValidatingToken: boolean;
  config: Config | null;

  // Actions
  setUser: (user: User | null) => void;
  setPricingTable: (table: PricingTable | null) => void;
  setClientSecret: (secret: string | null) => void;
  setError: (error: string | null) => void;
  setLoading: (loading: boolean) => void;
  setIsValidatingToken: (validating: boolean) => void;
  setConfig: (config: Config | null) => void;
  
  // Complex actions
  loadConfig: () => Promise<void>;
  validateToken: (token: string) => Promise<void>;
  fetchPricingTable: (token: string) => Promise<void>;
  fetchClientSecret: (token: string, teamId: number) => Promise<void>;
  initializeUpgrade: (token: string) => Promise<void>;
  reset: () => void;

  // Computed properties
  getStripeFormProps: () => StripeFormProps | null;
  isReady: () => boolean;
  isConfigLoading: () => boolean;
}

export const useUpgrade = create<UpgradeState>((set, get) => ({
  // Initial state
  user: null,
  pricingTable: null,
  clientSecret: null,
  error: null,
  loading: false,
  isValidatingToken: false,
  config: null,

  // Simple setters
  setUser: (user) => set({ user }),
  setPricingTable: (pricingTable) => set({ pricingTable }),
  setClientSecret: (clientSecret) => set({ clientSecret }),
  setError: (error) => set({ error }),
  setLoading: (loading) => set({ loading }),
  setIsValidatingToken: (isValidatingToken) => set({ isValidatingToken }),
  setConfig: (config) => set({ config }),

  // Complex actions
  loadConfig: async () => {
    try {
      const configStore = useConfig.getState();
      
      // If config store doesn't have config yet, try to load it
      if (!configStore.config) {
        await configStore.loadConfig();
      }
      
      // Get the config from the config store after loading
      const updatedConfigStore = useConfig.getState();
      const config = updatedConfigStore.config;
      console.log('load config', config);
      
      if (config) {
        set({ config, error: null });
      } else {
        set({ 
          error: 'Failed to load configuration. Please try again later.',
          config: null 
        });
      }
    } catch (error) {
      console.error('Error loading config:', error);
      set({ 
        error: 'Failed to load configuration. Please try again later.',
        config: null 
      });
    }
  },

  validateToken: async (token: string) => {
    if (!token) {
      set({
        error: 'No access token provided in URL. Please include ?token=your_jwt_token',
        isValidatingToken: false,
      });
      return;
    }

    try {
      set({ isValidatingToken: true, error: null });

      // Validate the JWT token using the validate-jwt endpoint
      const response = await getWithToken('/auth/validate-jwt', token);
      
      if (!response.ok) {
        throw new Error('Invalid or expired token');
      }

      // Get user data from /auth/me endpoint
      const userResponse = await getWithToken('/auth/me', token);
      const userData = await userResponse.json();
      
      set({ user: userData, isValidatingToken: false });
    } catch (err) {
      console.error('Error validating token:', err);
      set({
        error: err instanceof Error ? err.message : 'Failed to validate token',
        isValidatingToken: false,
      });
    }
  },

  fetchPricingTable: async (token: string) => {
    const { user } = get();
    if (!token || !user) return;

    try {
      set({ loading: true, error: null });
      const response = await getWithToken('/pricing-tables', token);
      const pricingTable = await response.json();
      set({ pricingTable, loading: false });
    } catch (err) {
      console.error('Error fetching pricing table:', err);
      set({
        error: 'Failed to load pricing table. Please try again later.',
        loading: false,
      });
    }
  },

  fetchClientSecret: async (token: string, teamId: number) => {
    if (!token || !teamId) return;

    try {
      set({ loading: true, error: null });
      const response = await getWithToken(
        `/billing/teams/${teamId}/pricing-table-session`,
        token
      );
      const data: PricingTableSession = await response.json();
      set({ clientSecret: data.client_secret, loading: false });
    } catch (err) {
      console.error('Error fetching pricing table session:', err);
      set({
        error: 'Failed to load pricing table session. Please try again later.',
        loading: false,
      });
    }
  },

  initializeUpgrade: async (token: string) => {
    const { loadConfig, validateToken, fetchPricingTable, fetchClientSecret } = get();
    
    // First load config and validate token in parallel
    await Promise.all([
      loadConfig(),
      validateToken(token),
    ]);
    
    const { user, error, config } = get();
    if (error || !user || !config) return;

    // Then fetch pricing table and client secret in parallel
    await Promise.all([
      fetchPricingTable(token),
      user.team_id ? fetchClientSecret(token, user.team_id) : Promise.resolve(),
    ]);
  },

  reset: () => set({
    user: null,
    pricingTable: null,
    clientSecret: null,
    error: null,
    loading: false,
    isValidatingToken: false,
    config: null,
  }),

  // Computed properties
  getStripeFormProps: () => {
    const { pricingTable, clientSecret, config } = get();
    
    if (!pricingTable || !clientSecret || !config?.STRIPE_PUBLISHABLE_KEY) {
      return null;
    }

    return {
      pricingTableId: pricingTable.pricing_table_id,
      publishableKey: config.STRIPE_PUBLISHABLE_KEY,
      clientSecret,
    };
  },

  isReady: () => {
    const { user, pricingTable, clientSecret, config, error, loading, isValidatingToken } = get();
    return !!(
      user &&
      pricingTable &&
      clientSecret &&
      config &&
      !error &&
      !loading &&
      !isValidatingToken
    );
  },

  isConfigLoading: () => {
    const configStore = useConfig.getState();
    return configStore.loading;
  },
}));
