let configCache = null;

export async function getConfig() {
  if (configCache) {
    return configCache;
  }

  try {
    const response = await fetch("/api/config");
    if (!response.ok) {
      throw new Error("Failed to load configuration");
    }
    configCache = await response.json();
    return configCache;
  } catch (error) {
    console.error("Error loading configuration:", error);
    // Fallback configuration
    return {
      NEXT_PUBLIC_API_URL:
        process.env.NEXT_PUBLIC_API_URL || "http://localhost:8800",
    };
  }
}

// Helper function to get API URL
export async function getApiUrl() {
  const config = await getConfig();
  return config.NEXT_PUBLIC_API_URL;
}

// Synchronous function to get cached config (use this when you can't use async/await)
export function getCachedConfig() {
  return (
    configCache || {
      NEXT_PUBLIC_API_URL:
        process.env.NEXT_PUBLIC_API_URL || "http://localhost:8800",
    }
  );
}
