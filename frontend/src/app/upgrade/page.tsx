"use client";

import { Loader2, AlertCircle } from "lucide-react";
import { useSearchParams } from "next/navigation";
import Script from "next/script";
import { useEffect, useRef, useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Card, CardContent } from "@/components/ui/card";
import { useUpgrade } from "@/stores/use-upgrade";
import { getWithToken } from "@/utils/api";

declare module "react" {
  interface HTMLAttributes<T> extends AriaAttributes, DOMAttributes<T> {
    "pricing-table-id"?: string;
    "publishable-key"?: string;
    "customer-session-client-secret"?: string;
    "client-reference-id"?: string;
  }
}

declare module "react/jsx-runtime" {
  interface Element {
    "stripe-pricing-table": HTMLElement;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "stripe-pricing-table": HTMLElement;
  }
}

export default function PricingTokenPage() {
  const searchParams = useSearchParams();
  const token = searchParams?.get("token");
  const initializedRef = useRef<string | null>(null);
  const [teamRegions, setTeamRegions] = useState<
    Array<{ id: number; name: string; label?: string | null }>
  >([]);
  const [selectedRegionId, setSelectedRegionId] = useState<number | null>(null);

  // Use the upgrade store
  const {
    user,
    error,
    loading,
    isValidatingToken,
    config,
    initializeUpgrade,
    getStripeFormProps,
    isConfigLoading,
    reset,
  } = useUpgrade();

  // Initialize upgrade flow when component mounts or token changes
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

  useEffect(() => {
    const fetchTeamRegions = async () => {
      if (!token || !user?.team_id) return;
      const response = await getWithToken(`/teams/${user.team_id}`, token);
      const data = await response.json();
      const regions = data.allowed_regions ?? [];
      setTeamRegions(regions);
      if (!selectedRegionId && regions[0]?.id) {
        setSelectedRegionId(regions[0].id);
      }
    };

    fetchTeamRegions().catch((err) => {
      console.error("Error fetching team regions:", err);
    });
  }, [token, user?.team_id, selectedRegionId]);

  // Loading state
  if (isValidatingToken || isConfigLoading() || loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <Card className="w-full max-w-md">
          <CardContent className="pt-6">
            <div className="flex items-center justify-center space-x-2">
              <Loader2 className="h-6 w-6 animate-spin" />
              <span>
                {isValidatingToken
                  ? "Validating access token..."
                  : isConfigLoading()
                    ? "Loading configuration..."
                    : "Loading pricing information..."}
              </span>
            </div>
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

  // Configuration missing state
  if (!config?.STRIPE_PUBLISHABLE_KEY) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <Card className="w-full max-w-md">
          <CardContent className="pt-6">
            <Alert variant="destructive">
              <AlertCircle className="h-4 w-4" />
              <AlertDescription>
                Stripe configuration is missing. Please contact support.
              </AlertDescription>
            </Alert>
          </CardContent>
        </Card>
      </div>
    );
  }

  // Get Stripe form properties
  const stripeFormProps = getStripeFormProps();
  const clientReferenceId =
    user?.team_id && selectedRegionId
      ? `${user.team_id}-${selectedRegionId}`
      : null;

  // Success state - show pricing table
  return (
    <div className="min-h-screen bg-gray-50 py-8">
      <div className="max-w-4xl mx-auto px-4">
        <div className="text-center mb-8">
          <h1 className="text-3xl font-bold text-gray-900 mb-2">
            Subscription Plans
          </h1>
          <p className="text-gray-600">
            Welcome, {user?.email}. Choose a plan that works for your team.
          </p>
        </div>

        <Script
          src="https://js.stripe.com/v3/pricing-table.js"
          strategy="afterInteractive"
        />

        {stripeFormProps ? (
          <div className="bg-white rounded-lg shadow-sm p-2">
            {teamRegions.length > 1 && (
              <div className="mb-4 max-w-md">
                <label
                  htmlFor="region-select"
                  className="block text-sm font-medium text-gray-700 mb-1"
                >
                  Region
                </label>
                <select
                  id="region-select"
                  value={selectedRegionId ?? ""}
                  onChange={(e) => setSelectedRegionId(Number(e.target.value))}
                  className="w-full border border-gray-300 rounded-md px-3 py-2"
                >
                  {teamRegions.map((region) => (
                    <option key={region.id} value={region.id}>
                      {region.label || region.name}
                    </option>
                  ))}
                </select>
              </div>
            )}
            {/* @ts-expect-error - Stripe pricing table is a custom element */}
            <stripe-pricing-table
              pricing-table-id={stripeFormProps.pricingTableId}
              publishable-key={stripeFormProps.publishableKey}
              customer-session-client-secret={stripeFormProps.clientSecret}
              client-reference-id={clientReferenceId ?? undefined}
            />
          </div>
        ) : (
          <Card>
            <CardContent className="pt-6">
              <div className="flex items-center justify-center space-x-2">
                <Loader2 className="h-6 w-6 animate-spin" />
                <span>Loading pricing information...</span>
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
