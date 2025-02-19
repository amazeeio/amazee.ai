import { getApiUrl } from './config';

const defaultOptions: RequestInit = {
  credentials: 'include',
  headers: {
    'Content-Type': 'application/json',
  },
};

export async function fetchApi(endpoint: string, options: RequestInit = {}) {
  const apiUrl = await getApiUrl();
  const url = `${apiUrl}${endpoint.startsWith('/') ? endpoint : `/${endpoint}`}`;

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

  if (!response.ok) {
    const error = await response.json().catch(() => ({ message: response.statusText }));
    throw new Error(error.detail || error.message || 'API call failed');
  }

  return response;
}

// Helper methods for common HTTP methods
export async function get(endpoint: string, options: RequestInit = {}) {
  return fetchApi(endpoint, { ...options, method: 'GET' });
}

export async function post(endpoint: string, data: any, options: RequestInit = {}) {
  return fetchApi(endpoint, {
    ...options,
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function put(endpoint: string, data: any, options: RequestInit = {}) {
  return fetchApi(endpoint, {
    ...options,
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

export async function del(endpoint: string, options: RequestInit = {}) {
  return fetchApi(endpoint, { ...options, method: 'DELETE' });
}