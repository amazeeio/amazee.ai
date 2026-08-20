"use client";

import { Loader2, AlertCircle } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Card, CardContent } from "@/components/ui/card";
import { useUpgrade } from "@/stores/use-upgrade";

/**
 * Landing page for the emailed sign-in links (`?token=<jwt>`).
 *
 * The token is the sign-in: validating it sets the access_token cookie, then
 * the visitor goes to the dashboard. Plan selection happens on my.amazee.io.
 */
export default function TokenSignInPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const token = searchParams?.get("token");
  const initializedRef = useRef<string | null>(null);

  const {
    user,
    error,
    loading,
    isValidatingToken,
    initializeUpgrade,
    isConfigLoading,
    reset,
  } = useUpgrade();

  // Initialize the flow when the component mounts or the token changes
  useEffect(() => {
    // Reset the initialized flag when token changes
    if (token !== initializedRef.current) {
      initializedRef.current = null;
    }

    if (token && !initializedRef.current) {
      initializeUpgrade(token);
      initializedRef.current = token;
    } else if (!token) {
      reset();
      initializedRef.current = null;
    }
  }, [token, initializeUpgrade, reset]);

  // The cookie is set once the token validates, so the dashboard can take over.
  useEffect(() => {
    if (user && !error) {
      router.replace("/");
    }
  }, [user, error, router]);

  // A visitor who lands here without a token has nothing to validate, so say so
  // instead of spinning.
  if (!token) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <Card className="w-full max-w-md">
          <CardContent className="pt-6">
            <Alert variant="destructive">
              <AlertCircle className="h-4 w-4" />
              <AlertDescription>
                This sign-in link is missing its token. Request a new link.
              </AlertDescription>
            </Alert>
          </CardContent>
        </Card>
      </div>
    );
  }

  // Error state
  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <Card className="w-full max-w-md">
          <CardContent className="pt-6">
            <Alert variant="destructive">
              <AlertCircle className="h-4 w-4" />
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <Card className="w-full max-w-md">
        <CardContent className="pt-6">
          <div className="flex items-center justify-center space-x-2">
            <Loader2 className="h-6 w-6 animate-spin" />
            <span>
              {isValidatingToken || isConfigLoading() || loading
                ? "Validating access token..."
                : "Signing you in..."}
            </span>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
