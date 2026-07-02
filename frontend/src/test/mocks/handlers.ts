import { http, HttpResponse } from "msw";

export const handlers = [
  // Global config fallback for client stores initialization
  http.get("/api/config", () => {
    return HttpResponse.json({
      NEXT_PUBLIC_API_URL: "http://localhost:8800",
      PASSWORDLESS_SIGN_IN: false,
      STRIPE_PUBLISHABLE_KEY: "",
    });
  }),

  // Auth endpoints
  http.post("http://localhost:8800/auth/login", () => {
    return HttpResponse.json({
      success: true,
      user: {
        id: 1,
        email: "test@example.com",
        name: "Test User",
        role: "user",
      },
    });
  }),

  http.post("http://localhost:8800/auth/logout", () => {
    return HttpResponse.json({ success: true });
  }),

  http.get("http://localhost:8800/auth/me", () => {
    return HttpResponse.json({
      id: 1,
      email: "test@example.com",
      name: "Test User",
      role: "user",
      team_id: 1,
    });
  }),

  // Teams endpoints
  http.get("http://localhost:8800/teams", () => {
    return HttpResponse.json([
      {
        id: 1,
        name: "Test Team",
        created_at: "2024-01-01T00:00:00Z",
        is_active: true,
        is_always_free: false,
        products: [],
      },
    ]);
  }),

  // Private AI Keys endpoints
  http.get("http://localhost:8800/private-ai-keys", () => {
    return HttpResponse.json([
      {
        id: 1,
        name: "Test Key",
        region: "us-east-1",
        label: "US East 1",
        status: "active",
        created_at: "2024-01-01T00:00:00Z",
      },
    ]);
  }),

  // Users endpoints
  http.get("http://localhost:8800/users", ({ request }) => {
    const url = new URL(request.url);
    const search = url.searchParams.get("search");

    const allUsers = [
      {
        id: 1,
        email: "test@example.com",
        name: "Test User",
        role: "user",
        team_id: 1,
        is_active: true,
        created_at: "2024-01-01T00:00:00Z",
      },
      {
        id: 2,
        email: "admin@example.com",
        name: "Admin User",
        role: "admin",
        team_id: 1,
        is_active: true,
        created_at: "2024-01-01T00:00:00Z",
      },
      {
        id: 3,
        email: "john@example.com",
        name: "John Doe",
        role: "user",
        team_id: 1,
        is_active: true,
        created_at: "2024-01-01T00:00:00Z",
      },
    ];

    if (search) {
      const filtered = allUsers.filter((u) =>
        u.email.toLowerCase().includes(search.toLowerCase()),
      );
      return HttpResponse.json(filtered);
    }

    return HttpResponse.json(allUsers);
  }),

  // Regions endpoints
  http.get("http://localhost:8800/regions/admin", () => {
    return HttpResponse.json([
      {
        id: "1",
        name: "us-east-1",
        label: "US East 1",
        description: "Test region description",
        postgres_host: "localhost",
        postgres_port: 5432,
        postgres_admin_user: "admin",
        litellm_api_url: "http://localhost:4000",
        is_active: true,
        is_dedicated: false,
      },
      {
        id: "2",
        name: "us-west-2",
        label: "US West 2",
        description: "Dedicated region",
        postgres_host: "localhost",
        postgres_port: 5432,
        postgres_admin_user: "admin",
        litellm_api_url: "http://localhost:4000",
        is_active: true,
        is_dedicated: true,
      },
    ]);
  }),

  http.post("http://localhost:8800/regions", async ({ request }) => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const data = (await request.json()) as any;
    return HttpResponse.json(
      {
        id: Math.random().toString(),
        ...data,
        is_active: true,
      },
      { status: 201 },
    );
  }),

  http.delete("http://localhost:8800/regions/:id", () => {
    return new HttpResponse(null, { status: 204 });
  }),

  http.get("http://localhost:8800/regions/:id/teams", () => {
    return HttpResponse.json([{ id: 1, name: "Test Team" }]);
  }),

  // Audit logs endpoints
  http.get("http://localhost:8800/audit/logs/metadata", () => {
    return HttpResponse.json({
      event_types: ["CREATE", "DELETE", "UPDATE", "LOGIN"],
      resource_types: ["user", "team", "key", "product"],
      status_codes: ["200", "201", "401", "403", "500"],
    });
  }),

  http.get("http://localhost:8800/audit/logs", ({ request }) => {
    const url = new URL(request.url);
    const skip = parseInt(url.searchParams.get("skip") || "0");
    const limit = parseInt(url.searchParams.get("limit") || "20");

    const allLogs = [
      {
        id: "1",
        timestamp: "2024-01-15T10:30:00Z",
        action: "create_user",
        details: { status_code: 201 },
        user_id: "1",
        event_type: "CREATE",
        resource_type: "user",
        user_email: "admin@example.com",
        request_source: "frontend",
        ip_address: "192.168.1.1",
      },
      {
        id: "2",
        timestamp: "2024-01-15T09:00:00Z",
        action: "login",
        details: { status_code: 200 },
        user_id: "2",
        event_type: "LOGIN",
        resource_type: "user",
        user_email: "test@example.com",
        request_source: "api",
        ip_address: "192.168.1.2",
      },
      {
        id: "3",
        timestamp: "2024-01-14T15:00:00Z",
        action: "delete_key",
        details: { status_code: 204 },
        user_id: "1",
        event_type: "DELETE",
        resource_type: "key",
        user_email: "admin@example.com",
        request_source: "frontend",
        ip_address: "192.168.1.1",
      },
    ];

    const paginatedLogs = allLogs.slice(skip, skip + limit);
    return HttpResponse.json({
      items: paginatedLogs,
      total: allLogs.length,
    });
  }),

  // Model Management endpoints
  http.get("http://localhost:8800/admin/models", () => {
    return HttpResponse.json([
      {
        id: 1,
        model_id: "meta-llama/llama-3-70b-instruct",
        display_name: "Llama 3 70B",
        provider: "meta",
        type: "chat",
        context_length: 128000,
        max_output_tokens: 4096,
        description: "Meta's flagship open-weights model",
        real_eol: "2026-12-31T23:59:59Z",
        override_eol: null,
        is_active_globally: true,
        litellm_params: {},
        created_at: "2024-01-01T00:00:00Z",
        updated_at: "2024-01-01T00:00:00Z",
        deleted_at: null,
        regions: [
          {
            region_id: 1,
            region_name: "us-east-1",
            is_active: true,
            sync_status: "synced",
            sync_error: null,
            synced_at: "2024-01-01T01:00:00Z",
          },
          {
            region_id: 2,
            region_name: "us-west-2",
            is_active: false,
            sync_status: "synced",
            sync_error: null,
            synced_at: null,
          }
        ],
      },
      {
        id: 2,
        model_id: "openai/gpt-4o-mini",
        display_name: "GPT-4o Mini",
        provider: "openai",
        type: "chat",
        context_length: 128000,
        max_output_tokens: 16384,
        description: "OpenAI's fast, cost-efficient small model",
        real_eol: null,
        override_eol: "2026-06-30T00:00:00Z",
        is_active_globally: true,
        litellm_params: {},
        created_at: "2024-02-01T00:00:00Z",
        updated_at: "2024-02-01T00:00:00Z",
        deleted_at: null,
        regions: [
          {
            region_id: 1,
            region_name: "us-east-1",
            is_active: true,
            sync_status: "synced",
            sync_error: null,
            synced_at: "2024-02-01T01:00:00Z",
          }
        ],
      }
    ]);
  }),

  http.post("http://localhost:8800/admin/models", async ({ request }) => {
    const data = (await request.json()) as any;
    return HttpResponse.json(
      {
        id: 3,
        ...data,
        regions: [
          {
            region_id: 1,
            region_name: "us-east-1",
            is_active: false,
            sync_status: "synced",
            sync_error: null,
            synced_at: null,
          }
        ],
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        deleted_at: null,
      },
      { status: 201 }
    );
  }),

  http.put("http://localhost:8800/admin/models/:id", async ({ request, params }) => {
    const data = (await request.json()) as any;
    return HttpResponse.json({
      id: Number(params.id),
      ...data,
      regions: [
        {
          region_id: 1,
          region_name: "us-east-1",
          is_active: true,
          sync_status: "synced",
          sync_error: null,
          synced_at: new Date().toISOString(),
        }
      ],
      updated_at: new Date().toISOString(),
    });
  }),

  http.delete("http://localhost:8800/admin/models/:id", ({ params }) => {
    return HttpResponse.json({
      id: Number(params.id),
      model_id: "meta-llama/llama-3-70b-instruct",
      display_name: "Llama 3 70B",
      provider: "meta",
      type: "chat",
      context_length: 128000,
      max_output_tokens: 4096,
      description: "Meta's flagship open-weights model",
      real_eol: "2026-12-31T23:59:59Z",
      override_eol: null,
      is_active_globally: false,
      litellm_params: {},
      created_at: "2024-01-01T00:00:00Z",
      updated_at: new Date().toISOString(),
      deleted_at: new Date().toISOString(),
      regions: [
        {
          region_id: 1,
          region_name: "us-east-1",
          is_active: false,
          sync_status: "synced",
          sync_error: null,
          synced_at: "2024-01-01T01:00:00Z",
        }
      ],
    });
  }),

  http.post("http://localhost:8800/admin/models/region-toggle", async ({ request }) => {
    const data = (await request.json()) as any;
    return HttpResponse.json({
      success: true,
      model_id: data.model_id,
      region_id: data.region_id,
      is_active: data.is_active,
    });
  }),

  // Add more handlers as needed for your specific API endpoints
];
