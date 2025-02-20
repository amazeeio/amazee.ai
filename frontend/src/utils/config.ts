interface Config {
  NEXT_PUBLIC_API_URL: string;
}

let configCache: Config | null = null;

export async function getConfig(): Promise<Config> {
  if (configCache) {
    return configCache;
  }

  try {
    const response = await fetch('/api/config');

    if (!response.ok) {
      throw new Error('Failed to load configuration');
    }

    const config: Config = await response.json();
    configCache = config;
    return config;
  } catch (error) {
    console.error('Error loading configuration:', error);
    // Fallback configuration
    const fallback: Config = {
      NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8800',
    };
    configCache = fallback;
    return fallback;
  }
}

// Helper function to get API URL
export async function getApiUrl(): Promise<string> {
  const config = await getConfig();
  return config.NEXT_PUBLIC_API_URL;
}

// Synchronous function to get cached config (use this when you can't use async/await)
export function getCachedConfig(): Config {
  return configCache || {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8800',
  };
}