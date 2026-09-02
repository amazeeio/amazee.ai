interface Config {
  NEXT_PUBLIC_API_URL: string;
  PASSWORDLESS_SIGN_IN: boolean;
}

let configCache: Config | null = null;
let configPromise: Promise<Config> | null = null;

// PASSWORDLESS_SIGN_IN comes only from the server via /api/config — it is not a
// NEXT_PUBLIC_ var, so process.env cannot read it in the browser (it previously
// always yielded false). The fallback uses an inert default and relies on the
// async /api/config load for the real value.
function fallbackConfig(): Config {
  return {
    NEXT_PUBLIC_API_URL:
      process.env.NEXT_PUBLIC_API_URL || "http://localhost:8800",
    PASSWORDLESS_SIGN_IN: false,
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
