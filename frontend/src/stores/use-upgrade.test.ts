import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { server } from '../test/mocks/server';
import { useUpgrade, User, PricingTable, PricingTableSession } from './use-upgrade';
import { useConfig } from './use-config';

// Mock environment variables
const originalEnv = process.env;

const mockUser: User = {
  id: 1,
  email: 'test@example.com',
  is_active: true,
  is_admin: false,
  team_id: 123,
  role: 'user',
};

const mockPricingTable: PricingTable = {
  pricing_table_id: 'prctbl_test123',
  updated_at: '2024-01-01T00:00:00Z',
};

const mockPricingTableSession: PricingTableSession = {
  client_secret: 'cuss_test_secret_123',
};

const mockConfig = {
  NEXT_PUBLIC_API_URL: 'http://localhost:8800',
  PASSWORDLESS_SIGN_IN: false,
  STRIPE_PUBLISHABLE_KEY: 'pk_test_123',
};

describe('useUpgrade store', () => {
  beforeEach(() => {
    // Reset environment variables
    process.env = { ...originalEnv };
    
    // Reset MSW handlers
    server.resetHandlers();
    
    // Add default config handler
    server.use(
      http.get('/api/config', () => {
        return HttpResponse.json(mockConfig);
      })
    );
    
    // Reset both stores before each test
    useConfig.getState().setConfig(null);
    useConfig.getState().setLoading(false);
    useConfig.getState().setError(null);
    useUpgrade.getState().reset();
  });

  afterEach(() => {
    // Restore environment variables
    process.env = originalEnv;
  });

  it('should initialize with empty state', () => {
    const state = useUpgrade.getState();
    
    expect(state.user).toBe(null);
    expect(state.pricingTable).toBe(null);
    expect(state.clientSecret).toBe(null);
    expect(state.error).toBe(null);
    expect(state.loading).toBe(false);
    expect(state.isValidatingToken).toBe(false);
    expect(state.config).toBe(null);
  });

  it('should reset state correctly', () => {
    // Set some state first
    useUpgrade.getState().setUser(mockUser);
    useUpgrade.getState().setError('test error');
    useUpgrade.getState().setLoading(true);
    useUpgrade.getState().setConfig(mockConfig);
    
    // Reset
    useUpgrade.getState().reset();
    
    const state = useUpgrade.getState();
    expect(state.user).toBe(null);
    expect(state.pricingTable).toBe(null);
    expect(state.clientSecret).toBe(null);
    expect(state.error).toBe(null);
    expect(state.loading).toBe(false);
    expect(state.isValidatingToken).toBe(false);
    expect(state.config).toBe(null);
  });

  describe('loadConfig', () => {
    it('should load config from config store successfully', async () => {
      // Pre-load config in config store
      useConfig.getState().setConfig(mockConfig);

      await useUpgrade.getState().loadConfig();
      
      const state = useUpgrade.getState();
      expect(state.config).toEqual(mockConfig);
      expect(state.error).toBe(null);
    });

    it('should load config when config store is empty', async () => {
      await useUpgrade.getState().loadConfig();
      
      const state = useUpgrade.getState();
      expect(state.config).toEqual(mockConfig);
      expect(state.error).toBe(null);
    });

    it('should handle config loading failure and use fallback', async () => {
      // Mock config API to fail
      server.use(
        http.get('/api/config', () => {
          return HttpResponse.error();
        })
      );

      await useUpgrade.getState().loadConfig();
      
      const state = useUpgrade.getState();
      // Should get fallback config from environment variables
      expect(state.config).toEqual({
        NEXT_PUBLIC_API_URL: 'http://localhost:8800',
        PASSWORDLESS_SIGN_IN: false,
        STRIPE_PUBLISHABLE_KEY: '',
      });
      expect(state.error).toBe(null); // No error because fallback worked
    });
  });

  describe('validateToken', () => {
    it('should validate token successfully and fetch user data', async () => {
      server.use(
        http.get('http://localhost:8800/auth/validate-jwt', () => {
          return HttpResponse.json({ valid: true });
        }),
        http.get('http://localhost:8800/auth/me', () => {
          return HttpResponse.json(mockUser);
        })
      );

      await useUpgrade.getState().validateToken('valid_token');
      
      const state = useUpgrade.getState();
      expect(state.user).toEqual(mockUser);
      expect(state.error).toBe(null);
      expect(state.isValidatingToken).toBe(false);
    });

    it('should handle empty token', async () => {
      await useUpgrade.getState().validateToken('');
      
      const state = useUpgrade.getState();
      expect(state.user).toBe(null);
      expect(state.error).toBe('No access token provided in URL. Please include ?token=your_jwt_token');
      expect(state.isValidatingToken).toBe(false);
    });

    it('should handle invalid token', async () => {
      server.use(
        http.get('http://localhost:8800/auth/validate-jwt', () => {
          return new HttpResponse(null, { status: 401 });
        })
      );

      await useUpgrade.getState().validateToken('invalid_token');
      
      const state = useUpgrade.getState();
      expect(state.user).toBe(null);
      expect(state.error).toBe('Unauthorized');
      expect(state.isValidatingToken).toBe(false);
    });

    it('should handle network error during token validation', async () => {
      server.use(
        http.get('http://localhost:8800/auth/validate-jwt', () => {
          return HttpResponse.error();
        })
      );

      await useUpgrade.getState().validateToken('valid_token');
      
      const state = useUpgrade.getState();
      expect(state.user).toBe(null);
      expect(state.error).toBe('Failed to fetch');
      expect(state.isValidatingToken).toBe(false);
    });
  });

  describe('fetchPricingTable', () => {
    beforeEach(() => {
      useUpgrade.getState().setUser(mockUser);
    });

    it('should fetch pricing table successfully', async () => {
      server.use(
        http.get('http://localhost:8800/pricing-tables', () => {
          return HttpResponse.json(mockPricingTable);
        })
      );

      await useUpgrade.getState().fetchPricingTable('valid_token');
      
      const state = useUpgrade.getState();
      expect(state.pricingTable).toEqual(mockPricingTable);
      expect(state.error).toBe(null);
      expect(state.loading).toBe(false);
    });

    it('should not fetch if no token provided', async () => {
      await useUpgrade.getState().fetchPricingTable('');
      
      const state = useUpgrade.getState();
      expect(state.pricingTable).toBe(null);
      expect(state.loading).toBe(false);
    });

    it('should not fetch if no user set', async () => {
      useUpgrade.getState().setUser(null);
      
      await useUpgrade.getState().fetchPricingTable('valid_token');
      
      const state = useUpgrade.getState();
      expect(state.pricingTable).toBe(null);
      expect(state.loading).toBe(false);
    });

    it('should handle fetch error', async () => {
      server.use(
        http.get('http://localhost:8800/pricing-tables', () => {
          return HttpResponse.error();
        })
      );

      await useUpgrade.getState().fetchPricingTable('valid_token');
      
      const state = useUpgrade.getState();
      expect(state.pricingTable).toBe(null);
      expect(state.error).toBe('Failed to load pricing table. Please try again later.');
      expect(state.loading).toBe(false);
    });
  });

  describe('fetchClientSecret', () => {
    it('should fetch client secret successfully', async () => {
      server.use(
        http.get('http://localhost:8800/billing/teams/123/pricing-table-session', () => {
          return HttpResponse.json(mockPricingTableSession);
        })
      );

      await useUpgrade.getState().fetchClientSecret('valid_token', 123);
      
      const state = useUpgrade.getState();
      expect(state.clientSecret).toBe(mockPricingTableSession.client_secret);
      expect(state.error).toBe(null);
      expect(state.loading).toBe(false);
    });

    it('should not fetch if no token provided', async () => {
      await useUpgrade.getState().fetchClientSecret('', 123);
      
      const state = useUpgrade.getState();
      expect(state.clientSecret).toBe(null);
      expect(state.loading).toBe(false);
    });

    it('should not fetch if no team ID provided', async () => {
      await useUpgrade.getState().fetchClientSecret('valid_token', 0);
      
      const state = useUpgrade.getState();
      expect(state.clientSecret).toBe(null);
      expect(state.loading).toBe(false);
    });

    it('should handle fetch error', async () => {
      server.use(
        http.get('http://localhost:8800/billing/teams/123/pricing-table-session', () => {
          return HttpResponse.error();
        })
      );

      await useUpgrade.getState().fetchClientSecret('valid_token', 123);
      
      const state = useUpgrade.getState();
      expect(state.clientSecret).toBe(null);
      expect(state.error).toBe('Failed to load pricing table session. Please try again later.');
      expect(state.loading).toBe(false);
    });
  });

  describe('initializeUpgrade', () => {
    it('should initialize upgrade flow successfully', async () => {
      server.use(
        http.get('http://localhost:8800/auth/validate-jwt', () => {
          return HttpResponse.json({ valid: true });
        }),
        http.get('http://localhost:8800/auth/me', () => {
          return HttpResponse.json(mockUser);
        }),
        http.get('http://localhost:8800/pricing-tables', () => {
          return HttpResponse.json(mockPricingTable);
        }),
        http.get('http://localhost:8800/billing/teams/123/pricing-table-session', () => {
          return HttpResponse.json(mockPricingTableSession);
        })
      );

      await useUpgrade.getState().initializeUpgrade('valid_token');
      
      const state = useUpgrade.getState();
      expect(state.user).toEqual(mockUser);
      expect(state.config).toEqual(mockConfig);
      expect(state.pricingTable).toEqual(mockPricingTable);
      expect(state.clientSecret).toBe(mockPricingTableSession.client_secret);
      expect(state.error).toBe(null);
    });

    it('should handle user without team_id', async () => {
      const userWithoutTeam = { ...mockUser, team_id: null };
      
      server.use(
        http.get('http://localhost:8800/auth/validate-jwt', () => {
          return HttpResponse.json({ valid: true });
        }),
        http.get('http://localhost:8800/auth/me', () => {
          return HttpResponse.json(userWithoutTeam);
        }),
        http.get('http://localhost:8800/pricing-tables', () => {
          return HttpResponse.json(mockPricingTable);
        })
      );

      await useUpgrade.getState().initializeUpgrade('valid_token');
      
      const state = useUpgrade.getState();
      expect(state.user).toEqual(userWithoutTeam);
      expect(state.config).toEqual(mockConfig);
      expect(state.pricingTable).toEqual(mockPricingTable);
      expect(state.clientSecret).toBe(null); // Should not fetch client secret without team_id
    });

    it('should stop initialization if token validation fails', async () => {
      server.use(
        http.get('http://localhost:8800/auth/validate-jwt', () => {
          return new HttpResponse(null, { status: 401 });
        })
      );

      await useUpgrade.getState().initializeUpgrade('invalid_token');
      
      const state = useUpgrade.getState();
      expect(state.user).toBe(null);
      expect(state.pricingTable).toBe(null);
      expect(state.clientSecret).toBe(null);
      expect(state.error).toBe('Unauthorized');
    });
  });

  describe('getStripeFormProps', () => {
    it('should return stripe form props when all data is available', () => {
      useUpgrade.getState().setPricingTable(mockPricingTable);
      useUpgrade.getState().setClientSecret(mockPricingTableSession.client_secret);
      useUpgrade.getState().setConfig(mockConfig);
      
      const props = useUpgrade.getState().getStripeFormProps();
      
      expect(props).toEqual({
        pricingTableId: mockPricingTable.pricing_table_id,
        publishableKey: mockConfig.STRIPE_PUBLISHABLE_KEY,
        clientSecret: mockPricingTableSession.client_secret,
      });
    });

    it('should return null when pricing table is missing', () => {
      useUpgrade.getState().setClientSecret(mockPricingTableSession.client_secret);
      useUpgrade.getState().setConfig(mockConfig);
      
      const props = useUpgrade.getState().getStripeFormProps();
      
      expect(props).toBe(null);
    });

    it('should return null when client secret is missing', () => {
      useUpgrade.getState().setPricingTable(mockPricingTable);
      useUpgrade.getState().setConfig(mockConfig);
      
      const props = useUpgrade.getState().getStripeFormProps();
      
      expect(props).toBe(null);
    });

    it('should return null when config is missing', () => {
      useUpgrade.getState().setPricingTable(mockPricingTable);
      useUpgrade.getState().setClientSecret(mockPricingTableSession.client_secret);
      
      const props = useUpgrade.getState().getStripeFormProps();
      
      expect(props).toBe(null);
    });

    it('should return null when stripe publishable key is missing from config', () => {
      const configWithoutStripe = { ...mockConfig, STRIPE_PUBLISHABLE_KEY: '' };
      useUpgrade.getState().setPricingTable(mockPricingTable);
      useUpgrade.getState().setClientSecret(mockPricingTableSession.client_secret);
      useUpgrade.getState().setConfig(configWithoutStripe);
      
      const props = useUpgrade.getState().getStripeFormProps();
      
      expect(props).toBe(null);
    });
  });

  describe('isReady', () => {
    it('should return true when all required data is available', () => {
      useUpgrade.getState().setUser(mockUser);
      useUpgrade.getState().setPricingTable(mockPricingTable);
      useUpgrade.getState().setClientSecret(mockPricingTableSession.client_secret);
      useUpgrade.getState().setConfig(mockConfig);
      
      expect(useUpgrade.getState().isReady()).toBe(true);
    });

    it('should return false when user is missing', () => {
      useUpgrade.getState().setPricingTable(mockPricingTable);
      useUpgrade.getState().setClientSecret(mockPricingTableSession.client_secret);
      useUpgrade.getState().setConfig(mockConfig);
      
      expect(useUpgrade.getState().isReady()).toBe(false);
    });

    it('should return false when pricing table is missing', () => {
      useUpgrade.getState().setUser(mockUser);
      useUpgrade.getState().setClientSecret(mockPricingTableSession.client_secret);
      useUpgrade.getState().setConfig(mockConfig);
      
      expect(useUpgrade.getState().isReady()).toBe(false);
    });

    it('should return false when client secret is missing', () => {
      useUpgrade.getState().setUser(mockUser);
      useUpgrade.getState().setPricingTable(mockPricingTable);
      useUpgrade.getState().setConfig(mockConfig);
      
      expect(useUpgrade.getState().isReady()).toBe(false);
    });

    it('should return false when config is missing', () => {
      useUpgrade.getState().setUser(mockUser);
      useUpgrade.getState().setPricingTable(mockPricingTable);
      useUpgrade.getState().setClientSecret(mockPricingTableSession.client_secret);
      
      expect(useUpgrade.getState().isReady()).toBe(false);
    });

    it('should return false when there is an error', () => {
      useUpgrade.getState().setUser(mockUser);
      useUpgrade.getState().setPricingTable(mockPricingTable);
      useUpgrade.getState().setClientSecret(mockPricingTableSession.client_secret);
      useUpgrade.getState().setConfig(mockConfig);
      useUpgrade.getState().setError('Something went wrong');
      
      expect(useUpgrade.getState().isReady()).toBe(false);
    });

    it('should return false when loading', () => {
      useUpgrade.getState().setUser(mockUser);
      useUpgrade.getState().setPricingTable(mockPricingTable);
      useUpgrade.getState().setClientSecret(mockPricingTableSession.client_secret);
      useUpgrade.getState().setConfig(mockConfig);
      useUpgrade.getState().setLoading(true);
      
      expect(useUpgrade.getState().isReady()).toBe(false);
    });

    it('should return false when validating token', () => {
      useUpgrade.getState().setUser(mockUser);
      useUpgrade.getState().setPricingTable(mockPricingTable);
      useUpgrade.getState().setClientSecret(mockPricingTableSession.client_secret);
      useUpgrade.getState().setConfig(mockConfig);
      useUpgrade.getState().setIsValidatingToken(true);
      
      expect(useUpgrade.getState().isReady()).toBe(false);
    });
  });

  describe('isConfigLoading', () => {
    it('should return true when config store is loading', () => {
      useConfig.getState().setLoading(true);
      
      expect(useUpgrade.getState().isConfigLoading()).toBe(true);
    });

    it('should return false when config store is not loading', () => {
      useConfig.getState().setLoading(false);
      
      expect(useUpgrade.getState().isConfigLoading()).toBe(false);
    });
  });

  describe('simple setters', () => {
    it('should set user correctly', () => {
      useUpgrade.getState().setUser(mockUser);
      expect(useUpgrade.getState().user).toEqual(mockUser);
    });

    it('should set pricing table correctly', () => {
      useUpgrade.getState().setPricingTable(mockPricingTable);
      expect(useUpgrade.getState().pricingTable).toEqual(mockPricingTable);
    });

    it('should set client secret correctly', () => {
      useUpgrade.getState().setClientSecret('test_secret');
      expect(useUpgrade.getState().clientSecret).toBe('test_secret');
    });

    it('should set error correctly', () => {
      useUpgrade.getState().setError('test error');
      expect(useUpgrade.getState().error).toBe('test error');
    });

    it('should set loading correctly', () => {
      useUpgrade.getState().setLoading(true);
      expect(useUpgrade.getState().loading).toBe(true);
    });

    it('should set isValidatingToken correctly', () => {
      useUpgrade.getState().setIsValidatingToken(true);
      expect(useUpgrade.getState().isValidatingToken).toBe(true);
    });

    it('should set config correctly', () => {
      useUpgrade.getState().setConfig(mockConfig);
      expect(useUpgrade.getState().config).toEqual(mockConfig);
    });
  });
});