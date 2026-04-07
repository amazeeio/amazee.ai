import { useState, useEffect } from "react";
import { getConfig, getCachedConfig } from "../utils/config";

export function useConfig() {
  const [config, setConfig] = useState(getCachedConfig());
  const [loading, setLoading] = useState(!config);
  const [error, setError] = useState(null);

  useEffect(() => {
    let mounted = true;

    async function loadConfig() {
      try {
        const freshConfig = await getConfig();
        if (mounted) {
          setConfig(freshConfig);
          setLoading(false);
        }
      } catch (err) {
        if (mounted) {
          setError(err);
          setLoading(false);
        }
      }
    }

    loadConfig();

    return () => {
      mounted = false;
    };
  }, []);

  return { config, loading, error };
}
