"use client";

import { create } from "zustand";

// Shared in-flight load so concurrent callers await the same request and get
// the real config (or a genuine null on error), never an ambiguous "still
// loading" null that callers would misread as a hard failure.
let inFlightLoad: Promise<Config | null> | null = null;

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

    if (inFlightLoad) {
      return inFlightLoad; // Await the existing load instead of a bare null
    }

    set({ loading: true, error: null });

    inFlightLoad = (async () => {
      try {
        const response = await fetch("/api/config");
        if (!response.ok) {
          throw new Error("Failed to load configuration");
        }

        const config: Config = await response.json();
        set({ config, loading: false, error: null, isLoaded: true });
        return config;
      } catch (error) {
        console.error("Error loading configuration:", error);
        // Fall back to build-time env values (wired via next.config env /
        // Docker build args) so the app stays usable when /api/config is
        // unavailable; isLoaded stays false so a later call retries.
        const fallback: Config = {
          NEXT_PUBLIC_API_URL:
            process.env.NEXT_PUBLIC_API_URL || "http://localhost:8800",
          PASSWORDLESS_SIGN_IN: process.env.PASSWORDLESS_SIGN_IN === "true",
          STRIPE_PUBLISHABLE_KEY: process.env.STRIPE_PUBLISHABLE_KEY || "",
        };
        set({
          config: fallback,
          loading: false,
          error:
            error instanceof Error
              ? error.message
              : "Failed to load configuration",
          isLoaded: false,
        });
        return fallback;
      } finally {
        inFlightLoad = null;
      }
    })();

    return inFlightLoad;
  },

  getApiUrl: () => {
    const state = get();
    return state.config?.NEXT_PUBLIC_API_URL || "http://localhost:8800";
  },
}));
