import { describe, it, expect, vi } from "vitest";
import { get, post } from "./api";

// Mock the config module to return a known API URL for testing
vi.mock("./config", () => ({
  getApiUrl: () => "http://localhost:8800",
}));

describe("API utilities with MSW", () => {
  it("should successfully fetch user data from mocked /api/auth/me endpoint", async () => {
    const response = await get("/api/auth/me");
    const userData = await response.json();

    expect(response.ok).toBe(true);
    expect(userData).toEqual({
      id: 1,
      email: "test@example.com",
      name: "Test User",
      role: "user",
      team_id: 1,
    });
  });

  it("should successfully post login data to mocked /api/auth/login endpoint", async () => {
    const loginData = {
      email: "test@example.com",
      password: "password123",
    };

    const response = await post("/api/auth/login", loginData);
    const responseData = await response.json();

    expect(response.ok).toBe(true);
    expect(responseData.success).toBe(true);
    expect(responseData.user.email).toBe("test@example.com");
  });

  it("should successfully fetch teams data from mocked endpoint", async () => {
    const response = await get("/api/teams");
    const teamsData = await response.json();

    expect(response.ok).toBe(true);
    expect(Array.isArray(teamsData)).toBe(true);
    expect(teamsData[0]).toEqual({
      id: 1,
      name: "Test Team",
      created_at: "2024-01-01T00:00:00Z",
    });
  });

  it("should successfully fetch private AI keys from mocked endpoint", async () => {
    const response = await get("/api/private-ai-keys");
    const keysData = await response.json();

    expect(response.ok).toBe(true);
    expect(Array.isArray(keysData)).toBe(true);
    expect(keysData[0]).toEqual({
      id: 1,
      name: "Test Key",
      region: "us-east-1",
      label: "US East 1",
      status: "active",
      created_at: "2024-01-01T00:00:00Z",
    });
  });
});
