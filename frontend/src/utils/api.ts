import { getApiUrl } from "./config";

let onUnauthorized: (() => void) | null = null;

export function setOnUnauthorized(callback: () => void) {
  onUnauthorized = callback;
}

const defaultOptions: RequestInit = {
  credentials: "include",
  headers: {
    "Content-Type": "application/json",
  },
};

export async function fetchApi(endpoint: string, options: RequestInit = {}): Promise<Response> {
  const apiUrl = await getApiUrl();
  const url = `${apiUrl}${endpoint.startsWith("/") ? endpoint : `/${endpoint}`}`;

  // Merge headers properly
  const headers = {
    ...defaultOptions.headers,
    ...options.headers,
  };

  const fetchOptions: RequestInit = {
    ...defaultOptions,
    ...options,
    headers,
  };

  const response = await fetch(url, fetchOptions);

  if (response.status === 401 && onUnauthorized) {
    onUnauthorized();
  }

  if (!response.ok) {
    if (response.status === 401 && typeof window !== 'undefined' && !window.location.pathname.startsWith('/auth/')) {
      // Clear the access token cookie to prevent middleware from redirecting back
      document.cookie = 'access_token=; path=/; expires=Thu, 01 Jan 1970 00:00:01 GMT;';
      window.location.href = '/auth/login?expired=true';
      // Return a promise that never resolves to stop execution while redirecting
      return new Promise(() => {}) as Promise<Response>;
    }
    const error = await response.json().catch(() => ({ message: response.statusText }));
    throw new Error(error.detail || error.message || 'API call failed');
  }

  return response;
}

// Helper methods for common HTTP methods
export async function get(endpoint: string, options: RequestInit = {}) {
  return fetchApi(endpoint, { ...options, method: "GET" });
}

export async function post<T extends Record<string, unknown>>(
  endpoint: string,
  data: T,
  options: RequestInit = {},
) {
  return fetchApi(endpoint, {
    ...options,
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function put<T extends Record<string, unknown>>(
  endpoint: string,
  data: T,
  options: RequestInit = {},
) {
  return fetchApi(endpoint, {
    ...options,
    method: "PUT",
    body: JSON.stringify(data),
  });
}

export async function del(endpoint: string, options: RequestInit = {}) {
  return fetchApi(endpoint, { ...options, method: "DELETE" });
}

// API functions that use Bearer token instead of cookies
export async function fetchApiWithToken(endpoint: string, token: string, options: RequestInit = {}): Promise<Response> {
  const apiUrl = await getApiUrl();
  const url = `${apiUrl}${endpoint.startsWith("/") ? endpoint : `/${endpoint}`}`;

  // Merge headers with Bearer token
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    ...options.headers,
  };

  const fetchOptions: RequestInit = {
    ...options,
    headers,
    // Don't include credentials when using Bearer token
    credentials: undefined,
  };

  const response = await fetch(url, fetchOptions);

  if (response.status === 401 && onUnauthorized) {
    onUnauthorized();
  }

  if (!response.ok) {
    if (response.status === 401 && typeof window !== 'undefined' && !window.location.pathname.startsWith('/auth/')) {
      // Clear the access token cookie to prevent middleware from redirecting back
      document.cookie = 'access_token=; path=/; expires=Thu, 01 Jan 1970 00:00:01 GMT;';
      window.location.href = '/auth/login?expired=true';
      // Return a promise that never resolves to stop execution while redirecting
      return new Promise(() => {}) as Promise<Response>;
    }
    const error = await response.json().catch(() => ({ message: response.statusText }));
    throw new Error(error.detail || error.message || 'API call failed');
  }

  return response;
}

export async function getWithToken(
  endpoint: string,
  token: string,
  options: RequestInit = {},
) {
  return fetchApiWithToken(endpoint, token, { ...options, method: "GET" });
}
