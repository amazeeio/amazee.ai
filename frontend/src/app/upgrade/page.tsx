"use client";

import { useEffect, useRef } from "react";
import { useSearchParams } from "next/navigation";
import Script from "next/script";
import { Card, CardContent } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Loader2, AlertCircle } from "lucide-react";
import { useUpgrade } from "@/stores/use-upgrade";

declare module "react" {
  interface HTMLAttributes<T> extends AriaAttributes, DOMAttributes<T> {
    "pricing-table-id"?: string;
    "publishable-key"?: string;
    "customer-session-client-secret"?: string;
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
            {/* @ts-expect-error - Stripe pricing table is a custom element */}
            <stripe-pricing-table
              pricing-table-id={stripeFormProps.pricingTableId}
              publishable-key={stripeFormProps.publishableKey}
              customer-session-client-secret={stripeFormProps.clientSecret}
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
