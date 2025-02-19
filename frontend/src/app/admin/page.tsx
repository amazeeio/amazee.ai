'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Users, Globe, Key } from 'lucide-react';

export default function AdminPage() {
  const router = useRouter();

  // Redirect to users page by default
  useEffect(() => {
    router.push('/admin/users');
  }, [router]);

  return (
    <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
      <Card className="cursor-pointer" onClick={() => router.push('/admin/users')}>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Users className="h-5 w-5" />
            Users
          </CardTitle>
          <CardDescription>Manage user accounts and permissions</CardDescription>
        </CardHeader>
        <CardContent>
          <Button variant="secondary" className="w-full">View Users</Button>
        </CardContent>
      </Card>

      <Card className="cursor-pointer" onClick={() => router.push('/admin/regions')}>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Globe className="h-5 w-5" />
            Regions
          </CardTitle>
          <CardDescription>Configure deployment regions and databases</CardDescription>
        </CardHeader>
        <CardContent>
          <Button variant="secondary" className="w-full">View Regions</Button>
        </CardContent>
      </Card>

      <Card className="cursor-pointer" onClick={() => router.push('/admin/private-ai-keys')}>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Key className="h-5 w-5" />
            Private AI Keys
          </CardTitle>
          <CardDescription>Manage private AI database credentials</CardDescription>
        </CardHeader>
        <CardContent>
          <Button variant="secondary" className="w-full">View Keys</Button>
        </CardContent>
      </Card>
    </div>
  );
}