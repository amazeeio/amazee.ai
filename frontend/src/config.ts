declare global {
  interface Window {
    RUNTIME_CONFIG?: {
      API_URL: string;
    };
  }
}

export const getConfig = () => {
  const runtimeConfig = window.RUNTIME_CONFIG;

  return {
    apiUrl: runtimeConfig?.API_URL || process.env.REACT_APP_API_URL || '',
  };
};

export default getConfig;