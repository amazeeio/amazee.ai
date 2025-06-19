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

  // Add more handlers as needed for your specific API endpoints
]