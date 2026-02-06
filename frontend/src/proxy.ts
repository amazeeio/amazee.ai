import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

export function proxy(request: NextRequest) {
  const token = request.cookies.get('access_token')?.value;
  const { pathname } = request.nextUrl;

  // Auth paths that should redirect to dashboard when logged in
  const authPaths = ['/auth/login', '/auth/register', '/auth/forgot-password', '/auth/reset-password', '/auth/verify-reset-code'];

  // Public paths that don't require authentication
  const publicPaths = [...authPaths, '/api/config', '/upgrade'];

  // Check if the path is public
  const isPublicPath = publicPaths.some(path => pathname.startsWith(path));

  // Check if the path is an auth path
  const isAuthPath = authPaths.some(path => pathname.startsWith(path));

  // Redirect to login if accessing a protected route without a token
  if (!token && !isPublicPath) {
    const url = new URL('/auth/login', request.url);
    url.searchParams.set('from', pathname);
    return NextResponse.redirect(url);
  }

  // Redirect to dashboard if accessing auth pages with a valid token
  if (token && isAuthPath) {
    return NextResponse.redirect(new URL('/private-ai-keys', request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    /*
     * Match all request paths except:
     * 1. /_next (Next.js internals)
     * 2. /_static (inside /public)
     * 3. all root files inside /public (e.g. /favicon.ico)
     */
    '/((?!_next|_static|_vercel|[\\w-]+\\.\\w+).*)',
  ],
};