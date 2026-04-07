"use client";

import { Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { ReactNode } from "react";
import { useEffect } from "react";
import { useAuth, isTeamAdmin } from "@/hooks/use-auth";
import { get } from "@/utils/api";
import { useQuery } from "@tanstack/react-query";

interface Team {
  id: number;
  name: string;
  admin_email: string;
  phone: string;
  billing_address: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export default function TeamAdminLayout({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const router = useRouter();

  const { data: team, isLoading: isLoadingTeam } = useQuery<Team>({
    queryKey: ["team", user?.team_id],
    queryFn: async () => {
      if (!user?.team_id) return null;
      const response = await get(`teams/${user.team_id}`);
      return response.json();
    },
    enabled: !!user?.team_id,
  });

  useEffect(() => {
    // Redirect if user is not a team admin
    if (!isTeamAdmin(user)) {
      router.push("/");
    }
  }, [user, router]);

  return (
    <div className="space-y-6">
      <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
        {isLoadingTeam ? (
          <div className="flex items-center justify-center py-4">
            <Loader2 className="h-6 w-6 animate-spin" />
          </div>
        ) : team ? (
          <h1 className="text-2xl font-bold mb-6">{team.name}</h1>
        ) : null}
        {children}
      </div>
    </div>
  );
}
