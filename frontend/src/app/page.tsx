"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { useAuth } from "@/hooks/use-auth";

export default function Home() {
  const router = useRouter();
  const { user } = useAuth();

  useEffect(() => {
    if (user) {
      // Redirect sales users to their dashboard
      if (user.role === "sales") {
        router.replace("/sales");
      } else {
        router.replace("/private-ai-keys");
      }
    } else {
      router.replace("/auth/login");
    }
  }, [user, router]);

  return null; // No need to render anything as we're redirecting
}
