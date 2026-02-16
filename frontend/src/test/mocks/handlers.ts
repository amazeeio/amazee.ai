import { http, HttpResponse } from 'msw'

export const handlers = [
  // Auth endpoints
  http.post('http://localhost:8800/api/auth/login', () => {
    return HttpResponse.json({
      success: true,
      user: {
        id: 1,
        email: 'test@example.com',
        name: 'Test User',
        role: 'user'
      }
    })
  }),

  http.post('http://localhost:8800/api/auth/logout', () => {
    return HttpResponse.json({ success: true })
  }),

  http.get('http://localhost:8800/api/auth/me', () => {
    return HttpResponse.json({
      id: 1,
      email: 'test@example.com',
      name: 'Test User',
      role: 'user',
      team_id: 1
    })
  }),

  // Teams endpoints
  http.get('http://localhost:8800/api/teams', () => {
    return HttpResponse.json([
      {
        id: 1,
        name: 'Test Team',
        created_at: '2024-01-01T00:00:00Z'
      }
    ])
  }),

  // Private AI Keys endpoints
  http.get('http://localhost:8800/api/private-ai-keys', () => {
    return HttpResponse.json([
      {
        id: 1,
        name: 'Test Key',
        region: 'us-east-1',
        label: 'US East 1',
        status: 'active',
        created_at: '2024-01-01T00:00:00Z'
      }
    ])
  }),

  // Users endpoints
  http.get('http://localhost:8800/api/users', () => {
    return HttpResponse.json([
      {
        id: 1,
        email: 'test@example.com',
        name: 'Test User',
        role: 'user',
        team_id: 1
      }
    ])
  }),

  // Regions endpoints
  http.get('http://localhost:8800/api/regions/admin', () => {
    return HttpResponse.json([
      {
        id: '1',
        name: 'us-east-1',
        label: 'US East 1',
        description: 'Test region description',
        postgres_host: 'localhost',
        postgres_port: 5432,
        postgres_admin_user: 'admin',
        litellm_api_url: 'http://localhost:4000',
        is_active: true,
        is_dedicated: false,
      },
      {
        id: '2',
        name: 'us-west-2',
        label: 'US West 2',
        description: 'Dedicated region',
        postgres_host: 'localhost',
        postgres_port: 5432,
        postgres_admin_user: 'admin',
        litellm_api_url: 'http://localhost:4000',
        is_active: true,
        is_dedicated: true,
      }
    ])
  }),

  http.post('http://localhost:8800/api/regions', async ({ request }) => {
    const data = await request.json() as any
    return HttpResponse.json({
      id: Math.random().toString(),
      ...data,
      is_active: true
    }, { status: 201 })
  }),

  http.delete('http://localhost:8800/api/regions/:id', () => {
    return new HttpResponse(null, { status: 204 })
  }),

  http.get('http://localhost:8800/api/regions/:id/teams', () => {
    return HttpResponse.json([
      { id: 1, name: 'Test Team' }
    ])
  }),

  // Users search endpoint
  http.get('http://localhost:8800/api/users', ({ request }) => {
    const url = new URL(request.url)
    const search = url.searchParams.get('search')
    
    const allUsers = [
      { id: 1, email: 'test@example.com', name: 'Test User', role: 'user', team_id: 1, is_active: true, created_at: '2024-01-01T00:00:00Z' },
      { id: 2, email: 'admin@example.com', name: 'Admin User', role: 'admin', team_id: 1, is_active: true, created_at: '2024-01-01T00:00:00Z' },
      { id: 3, email: 'john@example.com', name: 'John Doe', role: 'user', team_id: 1, is_active: true, created_at: '2024-01-01T00:00:00Z' },
    ]

    if (search) {
      const filtered = allUsers.filter(u => u.email.toLowerCase().includes(search.toLowerCase()))
      return HttpResponse.json(filtered)
    }
    
    return HttpResponse.json(allUsers)
  }),

  // Audit logs endpoints
  http.get('http://localhost:8800/api/audit/logs/metadata', () => {
    return HttpResponse.json({
      event_types: ['CREATE', 'DELETE', 'UPDATE', 'LOGIN'],
      resource_types: ['user', 'team', 'key', 'product'],
      status_codes: ['200', '201', '401', '403', '500']
    })
  }),

  http.get('http://localhost:8800/api/audit/logs', ({ request }) => {
    const url = new URL(request.url)
    const skip = parseInt(url.searchParams.get('skip') || '0')
    const limit = parseInt(url.searchParams.get('limit') || '20')

    const allLogs = [
      {
        id: '1',
        timestamp: '2024-01-15T10:30:00Z',
        action: 'create_user',
        details: { status_code: 201 },
        user_id: '1',
        event_type: 'CREATE',
        resource_type: 'user',
        user_email: 'admin@example.com',
        request_source: 'frontend',
        ip_address: '192.168.1.1'
      },
      {
        id: '2',
        timestamp: '2024-01-15T09:00:00Z',
        action: 'login',
        details: { status_code: 200 },
        user_id: '2',
        event_type: 'LOGIN',
        resource_type: 'user',
        user_email: 'test@example.com',
        request_source: 'api',
        ip_address: '192.168.1.2'
      },
      {
        id: '3',
        timestamp: '2024-01-14T15:00:00Z',
        action: 'delete_key',
        details: { status_code: 204 },
        user_id: '1',
        event_type: 'DELETE',
        resource_type: 'key',
        user_email: 'admin@example.com',
        request_source: 'frontend',
        ip_address: '192.168.1.1'
      }
    ]

    const paginatedLogs = allLogs.slice(skip, skip + limit)
    return HttpResponse.json({
      items: paginatedLogs,
      total: allLogs.length
    })
  }),

  // Add more handlers as needed for your specific API endpoints
]