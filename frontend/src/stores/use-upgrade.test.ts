import { http, HttpResponse } from "msw";
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { server } from "../test/mocks/server";
import { useConfig } from "./use-config";
import { useUpgrade, User } from "./use-upgrade";

// Mock environment variables
const originalEnv = process.env;

const mockUser: User = {
  id: 1,
  email: "test@example.com",
  is_active: true,
  is_admin: false,
  team_id: 123,
  role: "user",
};

const mockConfig = {
  NEXT_PUBLIC_API_URL: "http://localhost:8800",
  PASSWORDLESS_SIGN_IN: false,
};

describe("useUpgrade store", () => {
  beforeEach(() => {
    // Reset environment variables
    process.env = { ...originalEnv };

    // Reset MSW handlers
    server.resetHandlers();

    // Add default config handler
    server.use(
      http.get("/api/config", () => {
        return HttpResponse.json(mockConfig);
      }),
    );

    // Reset both stores before each test
    useConfig.setState({
      config: null,
      loading: false,
      error: null,
      isLoaded: false,
    });
    useUpgrade.getState().reset();
  });

  afterEach(() => {
    // Restore environment variables
    process.env = originalEnv;
  });

  it("should initialize with empty state", () => {
    const state = useUpgrade.getState();

    expect(state.user).toBe(null);
    expect(state.error).toBe(null);
    expect(state.loading).toBe(false);
    expect(state.isValidatingToken).toBe(false);
    expect(state.config).toBe(null);
  });

  it("should reset state correctly", () => {
    // Set some state first
    useUpgrade.getState().setUser(mockUser);
    useUpgrade.getState().setError("test error");
    useUpgrade.getState().setLoading(true);
    useUpgrade.getState().setConfig(mockConfig);

    // Reset
    useUpgrade.getState().reset();

    const state = useUpgrade.getState();
    expect(state.user).toBe(null);
    expect(state.error).toBe(null);
    expect(state.loading).toBe(false);
    expect(state.isValidatingToken).toBe(false);
    expect(state.config).toBe(null);
  });

  describe("loadConfig", () => {
    it("should load config from config store successfully", async () => {
      // Pre-load config in config store
      useConfig.getState().setConfig(mockConfig);

      await useUpgrade.getState().loadConfig();

      const state = useUpgrade.getState();
      expect(state.config).toEqual(mockConfig);
      expect(state.error).toBe(null);
    });

    it("should load config when config store is empty", async () => {
      await useUpgrade.getState().loadConfig();

      const state = useUpgrade.getState();
      expect(state.config).toEqual(mockConfig);
      expect(state.error).toBe(null);
    });

    it("should handle config loading failure and use fallback", async () => {
      // Mock config API to fail
      server.use(
        http.get("/api/config", () => {
          return HttpResponse.error();
        }),
      );

      await useUpgrade.getState().loadConfig();

      const state = useUpgrade.getState();
      // Should get fallback config from environment variables
      expect(state.config).toEqual({
        NEXT_PUBLIC_API_URL: "http://localhost:8800",
        PASSWORDLESS_SIGN_IN: false,
      });
      expect(state.error).toBe(null); // No error because fallback worked
    });
  });

  describe("validateToken", () => {
    it("should validate token successfully and fetch user data", async () => {
      server.use(
        http.get("http://localhost:8800/auth/validate-jwt", () => {
          return HttpResponse.json({ valid: true });
        }),
        http.get("http://localhost:8800/auth/me", () => {
          return HttpResponse.json(mockUser);
        }),
      );

      await useUpgrade.getState().validateToken("valid_token");

      const state = useUpgrade.getState();
      expect(state.user).toEqual(mockUser);
      expect(state.error).toBe(null);
      expect(state.isValidatingToken).toBe(false);
    });

    it("should handle empty token", async () => {
      await useUpgrade.getState().validateToken("");

      const state = useUpgrade.getState();
      expect(state.user).toBe(null);
      expect(state.error).toBe(
        "No access token provided in URL. Please include ?token=your_jwt_token",
      );
      expect(state.isValidatingToken).toBe(false);
    });

    it("should handle invalid token", async () => {
      server.use(
        http.get("http://localhost:8800/auth/validate-jwt", () => {
          return new HttpResponse(null, { status: 401 });
        }),
      );

      await useUpgrade.getState().validateToken("invalid_token");

      const state = useUpgrade.getState();
      expect(state.user).toBe(null);
      expect(state.error).toBe("Unauthorized");
      expect(state.isValidatingToken).toBe(false);
    });

    it("should handle network error during token validation", async () => {
      server.use(
        http.get("http://localhost:8800/auth/validate-jwt", () => {
          return HttpResponse.error();
        }),
      );

      await useUpgrade.getState().validateToken("valid_token");

      const state = useUpgrade.getState();
      expect(state.user).toBe(null);
      expect(state.error).toBe("Failed to fetch");
      expect(state.isValidatingToken).toBe(false);
    });

    it("should handle failure of the user lookup", async () => {
      server.use(
        http.get("http://localhost:8800/auth/validate-jwt", () => {
          return HttpResponse.json({ valid: true });
        }),
        http.get("http://localhost:8800/auth/me", () => {
          return HttpResponse.error();
        }),
      );

      await useUpgrade.getState().validateToken("valid_token");

      const state = useUpgrade.getState();
      expect(state.user).toBe(null);
      expect(state.error).toBe("Failed to fetch");
      expect(state.isValidatingToken).toBe(false);
    });
  });

  describe("initializeUpgrade", () => {
    it("should initialize upgrade flow successfully", async () => {
      server.use(
        http.get("http://localhost:8800/auth/validate-jwt", () => {
          return HttpResponse.json({ valid: true });
        }),
        http.get("http://localhost:8800/auth/me", () => {
          return HttpResponse.json(mockUser);
        }),
      );

      await useUpgrade.getState().initializeUpgrade("valid_token");

      const state = useUpgrade.getState();
      expect(state.user).toEqual(mockUser);
      expect(state.config).toEqual(mockConfig);
      expect(state.error).toBe(null);
      expect(useUpgrade.getState().isReady()).toBe(true);
    });

    it("should report the token error when validation fails", async () => {
      server.use(
        http.get("http://localhost:8800/auth/validate-jwt", () => {
          return new HttpResponse(null, { status: 401 });
        }),
      );

      await useUpgrade.getState().initializeUpgrade("invalid_token");

      const state = useUpgrade.getState();
      expect(state.user).toBe(null);
      expect(state.error).toBe("Unauthorized");
      expect(useUpgrade.getState().isReady()).toBe(false);
    });
  });

  describe("isReady", () => {
    it("should return true when all required data is available", () => {
      useUpgrade.getState().setUser(mockUser);
      useUpgrade.getState().setConfig(mockConfig);

      expect(useUpgrade.getState().isReady()).toBe(true);
    });

    it("should return false when user is missing", () => {
      useUpgrade.getState().setConfig(mockConfig);

      expect(useUpgrade.getState().isReady()).toBe(false);
    });

    it("should return false when config is missing", () => {
      useUpgrade.getState().setUser(mockUser);

      expect(useUpgrade.getState().isReady()).toBe(false);
    });

    it("should return false when there is an error", () => {
      useUpgrade.getState().setUser(mockUser);
      useUpgrade.getState().setConfig(mockConfig);
      useUpgrade.getState().setError("Something went wrong");

      expect(useUpgrade.getState().isReady()).toBe(false);
    });

    it("should return false when loading", () => {
      useUpgrade.getState().setUser(mockUser);
      useUpgrade.getState().setConfig(mockConfig);
      useUpgrade.getState().setLoading(true);

      expect(useUpgrade.getState().isReady()).toBe(false);
    });

    it("should return false when validating token", () => {
      useUpgrade.getState().setUser(mockUser);
      useUpgrade.getState().setConfig(mockConfig);
      useUpgrade.getState().setIsValidatingToken(true);

      expect(useUpgrade.getState().isReady()).toBe(false);
    });
  });

  describe("isConfigLoading", () => {
    it("should return true when config store is loading", () => {
      useConfig.getState().setLoading(true);

      expect(useUpgrade.getState().isConfigLoading()).toBe(true);
    });

    it("should return false when config store is not loading", () => {
      useConfig.getState().setLoading(false);

      expect(useUpgrade.getState().isConfigLoading()).toBe(false);
    });
  });

  describe("simple setters", () => {
    it("should set user correctly", () => {
      useUpgrade.getState().setUser(mockUser);
      expect(useUpgrade.getState().user).toEqual(mockUser);
    });

    it("should set error correctly", () => {
      useUpgrade.getState().setError("test error");
      expect(useUpgrade.getState().error).toBe("test error");
    });

    it("should set loading correctly", () => {
      useUpgrade.getState().setLoading(true);
      expect(useUpgrade.getState().loading).toBe(true);
    });

    it("should set isValidatingToken correctly", () => {
      useUpgrade.getState().setIsValidatingToken(true);
      expect(useUpgrade.getState().isValidatingToken).toBe(true);
    });

    it("should set config correctly", () => {
      useUpgrade.getState().setConfig(mockConfig);
      expect(useUpgrade.getState().config).toEqual(mockConfig);
    });
  });
});
