interface Config {
  NEXT_PUBLIC_API_URL: string;
  PASSWORDLESS_SIGN_IN: boolean;
  STRIPE_PUBLISHABLE_KEY: string;
}

let configCache: Config | null = null;
let configPromise: Promise<Config> | null = null;

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
      // Fallback configuration
      const fallback: Config = {
        NEXT_PUBLIC_API_URL:
          process.env.NEXT_PUBLIC_API_URL || "http://localhost:8800",
        PASSWORDLESS_SIGN_IN: process.env.PASSWORDLESS_SIGN_IN === "true",
        STRIPE_PUBLISHABLE_KEY: process.env.STRIPE_PUBLISHABLE_KEY || "",
      };
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
    // Initialize with fallback values
    configCache = {
      NEXT_PUBLIC_API_URL:
        process.env.NEXT_PUBLIC_API_URL || "http://localhost:8800",
      PASSWORDLESS_SIGN_IN: process.env.PASSWORDLESS_SIGN_IN === "true",
      STRIPE_PUBLISHABLE_KEY: process.env.STRIPE_PUBLISHABLE_KEY || "",
    };
    // Trigger async load
    getConfig().catch(console.error);
  }
  return configCache;
}
