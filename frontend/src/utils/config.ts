interface Config {
  NEXT_PUBLIC_API_URL: string;
  PASSWORDLESS_SIGN_IN: boolean;
  STRIPE_PUBLISHABLE_KEY: string;
}

let configCache: Config | null = null;
let configPromise: Promise<Config> | null = null;

// PASSWORDLESS_SIGN_IN and STRIPE_PUBLISHABLE_KEY come only from the server via
// /api/config — they are not NEXT_PUBLIC_ vars, so process.env cannot read them
// in the browser (it previously always yielded false/""). The fallback uses
// inert defaults and relies on the async /api/config load for the real values.
function fallbackConfig(): Config {
  return {
    NEXT_PUBLIC_API_URL:
      process.env.NEXT_PUBLIC_API_URL || "http://localhost:8800",
    PASSWORDLESS_SIGN_IN: false,
    STRIPE_PUBLISHABLE_KEY: "",
  };
}

export async function getConfig(): Promise<Config> {
  // If we have a cached value, return it
  if (configCache) {
    return configCache;
  }

  // If we have an in-flight request, return that promise
  if (configPromise) {
    return configPromise;
  }

  // Start a new request
  configPromise = (async () => {
    try {
      const response = await fetch("/api/config");
      if (!response.ok) {
        throw new Error("Failed to load configuration");
      }

      const config: Config = await response.json();
      configCache = config;
      return config;
    } catch (error) {
      console.error("Error loading configuration:", error);
      const fallback = fallbackConfig();
      configCache = fallback;
      return fallback;
    } finally {
      configPromise = null;
    }
  })();

  return configPromise;
}

// Helper function to get API URL
export async function getApiUrl(): Promise<string> {
  const config = await getConfig();
  return config.NEXT_PUBLIC_API_URL;
}

// Synchronous function to get cached config (use this when you can't use async/await)
export function getCachedConfig(): Config {
  if (!configCache) {
    // Seed with inert fallback, then trigger the async load for real values.
    configCache = fallbackConfig();
    getConfig().catch(console.error);
  }
  return configCache;
}
