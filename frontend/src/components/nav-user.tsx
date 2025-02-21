"use client"

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';

import { Button } from '@/components/ui/button';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { useAuth } from '@/hooks/use-auth';
import { useToast } from '@/hooks/use-toast';
import { getCachedConfig } from '@/utils/config';
import { User2, Key, LogOut, ChevronDown, Settings } from 'lucide-react';
import { cn } from '@/lib/utils';

interface NavUserProps {
  collapsed?: boolean;
}

export function NavUser({ collapsed }: NavUserProps) {
  const router = useRouter();
  const { user, setUser } = useAuth();
  const { toast } = useToast();

  const handleSignOut = async () => {
    try {
      const { NEXT_PUBLIC_API_URL: apiUrl } = getCachedConfig();
      const response = await fetch(`${apiUrl}/auth/logout`, {
        method: 'POST',
        credentials: 'include',
      });

      if (!response.ok) {
        throw new Error('Failed to logout');
      }

      // Clear the user state
      setUser(null);

      toast({
        title: 'Success',
        description: 'Successfully signed out',
      });

      // Redirect to login page
      router.push('/auth/login');
    } catch (error) {
      console.error('Logout error:', error);
      toast({
        title: 'Error',
        description: 'Failed to sign out',
        variant: 'destructive',
      });
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              className={cn(
                "w-full justify-start gap-2",
                collapsed && "px-2"
              )}
            >
              <User2 className="h-4 w-4" />
              {!collapsed && (
                <>
                  <span className="flex-1 text-left">{user?.email || 'Account'}</span>
                  <ChevronDown className="h-4 w-4" />
                </>
              )}
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            className={cn("w-56", collapsed && "ml-2")}
            align={collapsed ? "center" : "end"}
            side={collapsed ? "right" : "top"}
            forceMount
          >
            <DropdownMenuItem asChild>
              <Link href="/account" className="flex items-center gap-2">
                <Settings className="h-4 w-4" />
                <span>Account Settings</span>
              </Link>
            </DropdownMenuItem>
            <DropdownMenuItem asChild>
              <Link href="/auth/token" className="flex items-center gap-2">
                <Key className="h-4 w-4" />
                <span>API Tokens</span>
              </Link>
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={handleSignOut} className="gap-2">
              <LogOut className="h-4 w-4" />
              <span>Sign out</span>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  );
}
