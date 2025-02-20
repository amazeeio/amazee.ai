'use client';

import { Menu } from 'lucide-react';
import { Sidebar, SidebarProvider } from '@/components/ui/sidebar';
import { NavMain } from '@/components/nav-main';
import { NavUser } from '@/components/nav-user';
import { Sheet, SheetContent, SheetTrigger } from '@/components/ui/sheet';
import { Button } from '@/components/ui/button';
import { useEffect, useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useAuth } from '@/hooks/use-auth';

const navigation = [
  { name: 'Dashboard', href: '/dashboard' },
  {
    name: 'Admin',
    href: '/admin',
    subItems: [
      { name: 'Users', href: '/admin/users' },
      { name: 'Regions', href: '/admin/regions' },
      { name: 'Private AI Keys', href: '/admin/private-ai-keys' },
      { name: 'Audit Logs', href: '/admin/audit-logs' },
    ],
  },
];

export function SidebarLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const [isMounted, setIsMounted] = useState(false);
  const { user } = useAuth();

  useEffect(() => {
    setIsMounted(true);
  }, []);

  if (!isMounted) {
    return null;
  }

  // Don't show the sidebar on the auth login/register pages
  if (pathname === '/auth/login' || pathname === '/auth/register') {
    return <>{children}</>;
  }

  // Filter out admin navigation for non-admin users
  const filteredNavigation = navigation.filter(item => {
    if (item.name === 'Admin') {
      return user?.is_admin === true;
    }
    return true;
  });

  return (
    <div className="min-h-screen bg-background">
      {/* Desktop navigation */}
      <SidebarProvider defaultOpen>
        <Sidebar className="hidden lg:flex">
          <div className="flex h-16 items-center border-b px-6">
            <Link href="/dashboard" className="flex items-center space-x-2">
              <svg
                xmlns="http://www.w3.org/2000/svg"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                className="h-6 w-6"
              >
                <path d="M15 6v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3V6a3 3 0 1 0-3 3h12a3 3 0 1 0-3-3" />
              </svg>
              <span className="text-lg font-semibold">amazee.ai</span>
            </Link>
          </div>
          <NavMain navigation={filteredNavigation} pathname={pathname ?? '/'} />
          <NavUser />
        </Sidebar>

        {/* Mobile menu */}
        <Sheet>
          <SheetTrigger asChild>
            <Button
              variant="ghost"
              className="fixed left-4 top-4 px-0 text-base hover:bg-transparent focus-visible:bg-transparent focus-visible:ring-0 focus-visible:ring-offset-0 lg:hidden"
            >
              <Menu className="h-6 w-6" />
              <span className="sr-only">Toggle menu</span>
            </Button>
          </SheetTrigger>
          <SheetContent side="left" className="w-[300px] sm:w-[400px] p-0">
            <SidebarProvider>
              <Sidebar className="border-r-0">
                <div className="flex h-16 items-center border-b px-6">
                  <Link href="/dashboard" className="flex items-center space-x-2">
                    <svg
                      xmlns="http://www.w3.org/2000/svg"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      className="h-6 w-6"
                    >
                      <path d="M15 6v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3V6a3 3 0 1 0-3 3h12a3 3 0 1 0-3-3" />
                    </svg>
                    <span className="text-lg font-semibold">amazee.ai</span>
                  </Link>
                </div>
                <NavMain navigation={filteredNavigation} pathname={pathname ?? '/'} />
                <NavUser />
              </Sidebar>
            </SidebarProvider>
          </SheetContent>
        </Sheet>

        {/* Main content */}
        <main className="flex-1">
          <div className="px-4 pt-6 sm:px-6 lg:px-8">{children}</div>
        </main>
      </SidebarProvider>
    </div>
  );
}