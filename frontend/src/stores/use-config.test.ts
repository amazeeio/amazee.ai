import { http, HttpResponse } from "msw";
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { server } from "../test/mocks/server";
import { useConfig } from "./use-config";

// Mock environment variables
const originalEnv = process.env;

describe("useConfig store", () => {
  beforeEach(() => {
    // Reset environment variables
    process.env = { ...originalEnv };
    delete process.env.NEXT_PUBLIC_API_URL;
    delete process.env.PASSWORDLESS_SIGN_IN;
    delete process.env.STRIPE_PUBLISHABLE_KEY;

    // Reset MSW handlers
    server.resetHandlers();

    // Reset the store state before each test
    useConfig.setState({
      config: {
        NEXT_PUBLIC_API_URL: "http://localhost:8800",
        PASSWORDLESS_SIGN_IN: false,
        STRIPE_PUBLISHABLE_KEY: "",
      },
      loading: false,
      error: null,
      isLoaded: false,
    });
  });

  afterEach(() => {
    // Restore environment variables
    process.env = originalEnv;
  });

  it("should initialize with fallback config", () => {
    const state = useConfig.getState();

    expect(state.config).toEqual({
      NEXT_PUBLIC_API_URL: "http://localhost:8800",
      PASSWORDLESS_SIGN_IN: false,
      STRIPE_PUBLISHABLE_KEY: "",
    });
    expect(state.loading).toBe(false);
    expect(state.error).toBe(null);
  });

  it("should use environment variables for fallback config", () => {
    process.env.NEXT_PUBLIC_API_URL = "http://test-api.com";
    process.env.PASSWORDLESS_SIGN_IN = "true";
    process.env.STRIPE_PUBLISHABLE_KEY = "pk_test_123";

    // Reset the store to use the new environment variables
    useConfig.setState({
      config: {
        NEXT_PUBLIC_API_URL:
          process.env.NEXT_PUBLIC_API_URL || "http://localhost:8800",
        PASSWORDLESS_SIGN_IN: process.env.PASSWORDLESS_SIGN_IN === "true",
        STRIPE_PUBLISHABLE_KEY: process.env.STRIPE_PUBLISHABLE_KEY || "",
      },
      loading: false,
      error: null,
    });

    const state = useConfig.getState();
    expect(state.getApiUrl()).toBe("http://test-api.com");
    expect(state.config?.PASSWORDLESS_SIGN_IN).toBe(true);
    expect(state.config?.STRIPE_PUBLISHABLE_KEY).toBe("pk_test_123");
  });

  it("should load config successfully", async () => {
    const mockConfig = {
      NEXT_PUBLIC_API_URL: "http://api.example.com",
      PASSWORDLESS_SIGN_IN: true,
      STRIPE_PUBLISHABLE_KEY: "pk_test_456",
    };

    // Mock the config API endpoint
    server.use(
      http.get("/api/config", () => {
        return HttpResponse.json(mockConfig);
      }),
    );

    await useConfig.getState().loadConfig();

    const state = useConfig.getState();
    expect(state.config).toEqual(mockConfig);
    expect(state.loading).toBe(false);
    expect(state.error).toBe(null);
  });

  it("should handle fetch error and use fallback config", async () => {
    // Mock network error
    server.use(
      http.get("/api/config", () => {
        return HttpResponse.error();
      }),
    );

    await useConfig.getState().loadConfig();

    const state = useConfig.getState();
    expect(state.config).toEqual({
      NEXT_PUBLIC_API_URL: "http://localhost:8800",
      PASSWORDLESS_SIGN_IN: false,
      STRIPE_PUBLISHABLE_KEY: "",
    });
    expect(state.loading).toBe(false);
    expect(state.error).toBe("Failed to fetch");
  });

  it("should handle HTTP error response", async () => {
    // Mock HTTP 500 error
    server.use(
      http.get("/api/config", () => {
        return new HttpResponse(null, { status: 500 });
      }),
    );

    await useConfig.getState().loadConfig();

    const state = useConfig.getState();
    expect(state.config).toEqual({
      NEXT_PUBLIC_API_URL: "http://localhost:8800",
      PASSWORDLESS_SIGN_IN: false,
      STRIPE_PUBLISHABLE_KEY: "",
    });
    expect(state.loading).toBe(false);
    expect(state.error).toBe("Failed to load configuration");
  });

  it("should prevent multiple simultaneous requests", async () => {
    let requestCount = 0;

    // Mock a delayed response that tracks request count
    server.use(
      http.get("/api/config", async () => {
        requestCount++;
        // Add a small delay to simulate network request
        await new Promise((resolve) => setTimeout(resolve, 50));
        return HttpResponse.json({
          NEXT_PUBLIC_API_URL: "http://api.example.com",
          PASSWORDLESS_SIGN_IN: false,
          STRIPE_PUBLISHABLE_KEY: "",
        });
      }),
    );

    // Start first request
    const promise1 = useConfig.getState().loadConfig();
    expect(useConfig.getState().loading).toBe(true);

    // Start second request while first is pending
    const promise2 = useConfig.getState().loadConfig();

    // Both promises should resolve
    await Promise.all([promise1, promise2]);

    // Only one request should have been made due to deduplication
    expect(requestCount).toBe(1);
    expect(useConfig.getState().loading).toBe(false);
  });

  it("should return correct API URL", () => {
    const mockConfig = {
      NEXT_PUBLIC_API_URL: "http://custom-api.com",
      PASSWORDLESS_SIGN_IN: false,
      STRIPE_PUBLISHABLE_KEY: "",
    };

    useConfig.getState().setConfig(mockConfig);

    expect(useConfig.getState().getApiUrl()).toBe("http://custom-api.com");
  });

  it("should return fallback API URL when config is null", () => {
    useConfig.setState({ config: null });

    expect(useConfig.getState().getApiUrl()).toBe("http://localhost:8800");
  });

  it("should set config correctly", () => {
    const newConfig = {
      NEXT_PUBLIC_API_URL: "http://new-api.com",
      PASSWORDLESS_SIGN_IN: true,
      STRIPE_PUBLISHABLE_KEY: "pk_new_123",
    };

    useConfig.getState().setConfig(newConfig);

    const state = useConfig.getState();
    expect(state.config).toEqual(newConfig);
    expect(state.error).toBe(null);
  });

  it("should set loading state correctly", () => {
    useConfig.getState().setLoading(true);
    expect(useConfig.getState().loading).toBe(true);

    useConfig.getState().setLoading(false);
    expect(useConfig.getState().loading).toBe(false);
  });

  it("should set error state correctly", () => {
    const errorMessage = "Test error";

    useConfig.getState().setError(errorMessage);

    const state = useConfig.getState();
    expect(state.error).toBe(errorMessage);
    expect(state.loading).toBe(false);
  });

  it("should clear error when setting error to null", () => {
    useConfig.getState().setError("Initial error");
    expect(useConfig.getState().error).toBe("Initial error");

    useConfig.getState().setError(null);
    expect(useConfig.getState().error).toBe(null);
  });
});
