"use client";

import Script from "next/script";
import { useEffect, useState } from "react";
import { useAuth } from "@/hooks/use-auth";
import { get, post } from "@/utils/api";
import { useQuery } from "@tanstack/react-query";

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

interface PricingTable {
  pricing_table_id: string;
  stripe_publishable_key: string;
  updated_at: string;
}

interface TeamRegion {
  id: number;
  name: string;
  label?: string | null;
}

interface TeamDetails {
  id: number;
  allowed_regions: TeamRegion[];
}

export default function PricingPage() {
  const { user } = useAuth();
  const [clientSecret, setClientSecret] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedRegionId, setSelectedRegionId] = useState<number | null>(null);

  // Fetch pricing table data
  const {
    data: pricingTable,
    error: pricingTableError,
    isLoading: isLoadingPricingTable,
  } = useQuery<PricingTable>({
    queryKey: ["pricing-table"],
    queryFn: async () => {
      const response = await get("/pricing-tables");
      return response.json();
    },
  });

  const { data: teamDetails } = useQuery<TeamDetails>({
    queryKey: ["team-details", user?.team_id],
    queryFn: async () => {
      const response = await get(`/teams/${user?.team_id}`);
      return response.json();
    },
    enabled: !!user?.team_id,
  });

  useEffect(() => {
    const firstRegionId = teamDetails?.allowed_regions?.[0]?.id;
    if (!selectedRegionId && firstRegionId) {
      setSelectedRegionId(firstRegionId);
    }
  }, [teamDetails?.allowed_regions, selectedRegionId]);

  useEffect(() => {
    const fetchSessionToken = async () => {
      try {
        if (!user?.team_id) return;
        const response = await get(
          `/billing/teams/${user.team_id}/pricing-table-session`,
        );
        const data = await response.json();
        setClientSecret(data.client_secret);
      } catch (err) {
        setError("Failed to load pricing table. Please try again later.");
        console.error("Error fetching pricing table session:", err);
      }
    };

    fetchSessionToken();
  }, [user?.team_id]);

  const handleManageSubscription = async () => {
    try {
      const response = await post(`/billing/teams/${user?.team_id}/portal`, {});
      if (response.redirected) {
        window.location.href = response.url;
      }
    } catch (error) {
      console.error("Error accessing portal:", error);
    }
  };

  if (isLoadingPricingTable) {
    return <div>Loading pricing table...</div>;
  }

  if (error || pricingTableError) {
    return (
      <div className="text-red-500">
        {error || "Failed to load pricing table. Please try again later."}
      </div>
    );
  }

  if (!pricingTable?.stripe_publishable_key) {
    return (
      <div className="text-red-500">
        Stripe configuration is missing. Please contact support.
      </div>
    );
  }

  const regions = teamDetails?.allowed_regions ?? [];
  const clientReferenceId =
    user?.team_id && selectedRegionId
      ? `${user.team_id}-${selectedRegionId}`
      : null;

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h2 className="text-xl font-semibold">Subscription Plans</h2>
        <button
          onClick={handleManageSubscription}
          className="text-blue-600 hover:text-blue-800"
        >
          Manage Subscription
        </button>
      </div>
      <Script
        src="https://js.stripe.com/v3/pricing-table.js"
        strategy="afterInteractive"
      />
      {regions.length > 1 && (
        <div className="max-w-md">
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
            {regions.map((region) => (
              <option key={region.id} value={region.id}>
                {region.label || region.name}
              </option>
            ))}
          </select>
        </div>
      )}
      {clientSecret && pricingTable && clientReferenceId && (
        // @ts-expect-error - Stripe pricing table is a custom element
        <stripe-pricing-table
          pricing-table-id={pricingTable.pricing_table_id}
          publishable-key={pricingTable.stripe_publishable_key}
          customer-session-client-secret={clientSecret}
          client-reference-id={clientReferenceId}
        />
      )}
    </div>
  );
}
