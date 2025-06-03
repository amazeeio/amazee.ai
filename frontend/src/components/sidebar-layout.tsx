'use client';

import {
  Menu,
  Settings,
  Users,
  Globe,
  Key,
  ScrollText,
  ChevronDown,
  PanelLeftClose,
  PanelLeft,
  Users2,
  Package
} from 'lucide-react';
import { Sidebar, SidebarProvider } from '@/components/ui/sidebar';
import { NavUser } from '@/components/nav-user';
import { Sheet, SheetContent, SheetTrigger } from '@/components/ui/sheet';
import { Button } from '@/components/ui/button';
import { useEffect, useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useAuth, isTeamAdmin } from '@/hooks/use-auth';
import { cn } from '@/lib/utils';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';

interface NavItem {
  name: string;
  href: string;
  icon?: React.ReactNode;
  subItems?: NavItem[];
}

const navigation = [
  { name: 'Private AI Keys', href: '/private-ai-keys', icon: <Key size={16} /> },
  {
    name: 'Admin',
    href: '/admin',
    icon: <Settings size={16} />,
    subItems: [
      { name: 'Teams', href: '/admin/teams', icon: <Users2 size={16} /> },
      { name: 'Users', href: '/admin/users', icon: <Users size={16} /> },
      { name: 'Regions', href: '/admin/regions', icon: <Globe size={16} /> },
      { name: 'Products', href: '/admin/products', icon: <Package size={16} /> },
      { name: 'Private AI Keys', href: '/admin/private-ai-keys', icon: <Key size={16} /> },
      { name: 'Audit Logs', href: '/admin/audit-logs', icon: <ScrollText size={16} /> },
    ],
  },
  {
    name: 'Team Admin',
    href: '/team-admin',
    icon: <Users2 size={16} />,
    subItems: [
      { name: 'Users', href: '/team-admin/users', icon: <Users size={16} /> },
      { name: 'Private AI Keys', href: '/team-admin/private-ai-keys', icon: <Key size={16} /> },
      { name: 'Pricing', href: '/team-admin/pricing', icon: <Package size={16} /> },
    ],
  },
];

function NavMain({ navigation, pathname, collapsed }: { navigation: NavItem[]; pathname: string; collapsed: boolean }) {
  const [expandedItems, setExpandedItems] = useState<string[]>(['/admin', '/team-admin']);

  const toggleExpanded = (href: string) => {
    if (!collapsed) {
      setExpandedItems((prev) =>
        prev.includes(href)
          ? prev.filter((item) => item !== href)
          : [...prev, href]
      );
    }
  };

  const renderNavItem = (item: NavItem, level = 0) => {
    const isActive = pathname === item.href;
    const hasSubItems = item.subItems && item.subItems.length > 0;
    const isExpanded = expandedItems.includes(item.href);
    const isSubItemActive = hasSubItems && item.subItems?.some(
      (subItem) => pathname === subItem.href
    );

    const itemContent = (
      <span className="flex items-center gap-2">
        {item.icon && <div className="h-4 w-4 flex-shrink-0">{item.icon}</div>}
        {!collapsed && <span>{item.name}</span>}
      </span>
    );

    if (collapsed && hasSubItems) {
      return (
        <div key={item.href}>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                className={cn(
                  "flex w-full items-center justify-start rounded-lg px-2.5 py-1.5 transition-all",
                  isActive || isSubItemActive
                    ? "bg-accent text-accent-foreground"
                    : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                )}
              >
                {itemContent}
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent
              className="w-56 ml-2"
              align="center"
              side="right"
            >
              {item.subItems?.map((subItem) => (
                <DropdownMenuItem key={subItem.href} asChild>
                  <Link href={subItem.href} className="flex items-center gap-2">
                    {subItem.icon && <div className="h-4 w-4 flex-shrink-0">{subItem.icon}</div>}
                    <span>{subItem.name}</span>
                  </Link>
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      );
    }

    return (
      <div key={item.href}>
        <Link
          href={hasSubItems ? '#' : item.href}
          onClick={hasSubItems ? () => toggleExpanded(item.href) : undefined}
          className={`flex items-center justify-between rounded-lg px-2.5 py-1.5 transition-all ${
            level > 0 ? 'ml-4' : ''
          } ${
            isActive || isSubItemActive
              ? 'bg-accent text-accent-foreground'
              : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground'
          }`}
        >
          {itemContent}
          {!collapsed && hasSubItems && (
            <ChevronDown
              size={16}
              className={`transition-transform ${
                isExpanded ? 'rotate-180' : ''
              }`}
            />
          )}
        </Link>
        {!collapsed && hasSubItems && isExpanded && item.subItems && (
          <div className="mt-1">
            {item.subItems.map((subItem) => renderNavItem(subItem, level + 1))}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="flex-1 overflow-auto">
      <nav className="grid items-start px-4 text-sm font-medium gap-1">
        {navigation.map((item) => renderNavItem(item))}
      </nav>
    </div>
  );
}

export function SidebarLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const [isMounted, setIsMounted] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
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
  const filteredNavigation = navigation.filter((item) => {
    if (item.name === 'Admin' && !user?.is_admin) return false;
    if (item.name === 'Team Admin' && !isTeamAdmin(user)) return false;
    return true;
  });

  return (
    <div className="min-h-screen bg-background">
      {/* Desktop navigation */}
      <SidebarProvider defaultOpen>
        <Sidebar className={cn("hidden lg:flex transition-all duration-300", collapsed ? "w-[70px]" : "w-[240px]")}>
          <div className={cn("flex h-16 items-center border-b px-6 transition-all", collapsed ? "justify-center px-2" : "justify-between")}>
            {!collapsed && (
              <Link href="/private-ai-keys" className="flex items-center space-x-3">
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
            )}
            <Button
              variant="ghost"
              size="icon"
              className="h-6 w-6"
              onClick={() => setCollapsed(!collapsed)}
            >
              {collapsed ? <PanelLeft className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
            </Button>
          </div>
          <NavMain navigation={filteredNavigation} pathname={pathname ?? '/'} collapsed={collapsed} />
          <NavUser collapsed={collapsed} />
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
                  <Link href="/private-ai-keys" className="flex items-center space-x-3">
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
                <NavMain navigation={filteredNavigation} pathname={pathname ?? '/'} collapsed={false} />
                <NavUser collapsed={false} />
              </Sidebar>
            </SidebarProvider>
          </SheetContent>
        </Sheet>

        {/* Main content */}
        <main className="flex-1 transition-all duration-300">
          <div className="h-full w-full pl-0 pr-2 pt-6 sm:pr-4 lg:pr-6">{children}</div>
        </main>
      </SidebarProvider>
    </div>
  );
}