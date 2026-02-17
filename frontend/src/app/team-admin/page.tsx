"use client";

import { Users, Key } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export default function TeamAdminPage() {
  const router = useRouter();

  // Redirect to team users page by default
  useEffect(() => {
    router.push("/team-admin/users");
  }, [router]);

  return (
    <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
      <Card
        className="cursor-pointer"
        onClick={() => router.push("/team-admin/users")}
      >
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Users className="h-5 w-5" />
            Team Users
          </CardTitle>
          <CardDescription>Manage team members and their roles</CardDescription>
        </CardHeader>
        <CardContent>
          <Button variant="secondary" className="w-full">
            View Team Users
          </Button>
        </CardContent>
      </Card>

      <Card
        className="cursor-pointer"
        onClick={() => router.push("/team-admin/private-ai-keys")}
      >
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Key className="h-5 w-5" />
            Team AI Keys
          </CardTitle>
          <CardDescription>
            Manage team&apos;s private AI database credentials
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Button variant="secondary" className="w-full">
            View Team Keys
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
