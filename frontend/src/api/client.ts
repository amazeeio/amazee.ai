import axios, { AxiosError } from 'axios';

const API_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000';

export const api = axios.create({
  baseURL: API_URL,
  withCredentials: true, // Important for cookie handling
});

interface APIError {
  detail: string;
}

// Add response interceptor to handle errors
api.interceptors.response.use(
  (response) => response,
  (error: AxiosError<APIError>) => {
    if (error.response?.data?.detail) {
      error.message = error.response.data.detail;
    }
    return Promise.reject(error);
  }
);

export interface LoginCredentials {
  username: string;  // Keep as username for OAuth2 compatibility
  password: string;
}

export interface RegisterData {
  email: string;
  password: string;
}

export interface APIToken {
  id: number;
  name: string;
  token: string;
  created_at: string;
  last_used_at: string | null;
}

export interface User {
  id: number;
  email: string;
  is_active: boolean;
  is_admin: boolean;
  api_tokens: APIToken[];
}

export interface Region {
  id: number;
  name: string;
  postgres_host: string;
  postgres_port: number;
  postgres_admin_user: string;
  postgres_admin_password: string;
  litellm_api_url: string;
  litellm_api_key: string;
  is_active: boolean;
  created_at: string;
}

export interface RegionCreate {
  name: string;
  postgres_host: string;
  postgres_port: number;
  postgres_admin_user: string;
  postgres_admin_password: string;
  litellm_api_url: string;
  litellm_api_key: string;
}

export interface PrivateAIKey {
  id: string;
  database_name: string;
  host: string;
  username: string;  // This is the database username, not the user's email
  password?: string;
  litellm_token?: string;
  owner_id: number;
  region?: string;
}

export const auth = {
  login: async (credentials: LoginCredentials) => {
    const params = new URLSearchParams();
    params.append('username', credentials.username);  // OAuth2 expects 'username'
    params.append('password', credentials.password);
    const { data } = await api.post('/auth/login', params, {
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded'
      }
    });
    return data;
  },

  logout: async () => {
    await api.post('/auth/logout');
  },

  register: async (userData: RegisterData) => {
    const { data } = await api.post('/auth/register', userData, {
      headers: {
        'Content-Type': 'application/json'
      }
    });
    return data;
  },

  me: async () => {
    const { data } = await api.get<User>('/auth/me');
    return data;
  },
};

export const users = {
  list: async () => {
    const { data } = await api.get<User[]>('/users/all');
    return data;
  },

  update: async (userId: number, updateData: { is_admin?: boolean }) => {
    const { data } = await api.put<User>(`/users/${userId}`, updateData);
    return data;
  },
};

export const tokens = {
  list: async () => {
    const { data } = await api.get<APIToken[]>('/tokens');
    return data;
  },

  create: async (name: string) => {
    const { data } = await api.post<APIToken>('/tokens', { name });
    return data;
  },

  delete: async (tokenId: number) => {
    await api.delete(`/tokens/${tokenId}`);
  },
};

export const regions = {
  list: async () => {
    const { data } = await api.get<Region[]>('/regions');
    return data;
  },

  create: async (regionData: RegionCreate) => {
    const { data } = await api.post<Region>('/regions', regionData);
    return data;
  },

  delete: async (regionId: number) => {
    await api.delete(`/regions/${regionId}`);
  },
};

export const privateAIKeys = {
  list: async () => {
    try {
      const { data } = await api.get<PrivateAIKey[]>('/private-ai-keys');
      return data;
    } catch (error) {
      throw error;
    }
  },

  create: async (params: { region_id: number }) => {
    try {
      const { data } = await api.post<PrivateAIKey>('/private-ai-keys', params, {
        headers: {
          'Content-Type': 'application/json'
        }
      });
      return data;
    } catch (error) {
      throw error;
    }
  },

  delete: async (keyId: string) => {
    try {
      await api.delete(`/private-ai-keys/${keyId}`);
    } catch (error) {
      throw error;
    }
  }
};