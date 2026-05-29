"use client";

import { Loader2, RefreshCw, Info } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { get } from "@/utils/api";
import { useQuery } from "@tanstack/react-query";
import { Region } from "@/types/region";

interface SpendInfo {
  spend: number;
  expires: string;
  created_at: string;
  updated_at: string;
  max_budget: number | null;
  budget_duration: string | null;
  budget_reset_at: string | null;
}

interface PrivateAIKeySpendCellProps {
  keyId: number;
  hasLiteLLMToken: boolean;
  region?: string;
  teamId?: number;
  regions?: Region[];
}

export function PrivateAIKeySpendCell({
  keyId,
  hasLiteLLMToken,
  region,
  teamId,
  regions = [],
}: PrivateAIKeySpendCellProps) {
  const [isLoaded, setIsLoaded] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);

  const matchedRegion =
    teamId && region ? regions.find((r) => r.name === region) : undefined;

  // Query for spend data - only enabled when isLoaded is true
  // For team keys, also wait until the region can be resolved from the regions list
  const {
    data: spendData,
    isLoading,
    refetch,
  } = useQuery<SpendInfo>({
    queryKey: ["private-ai-key-spend", keyId, region, teamId, regions.map((r) => r.id)],
    queryFn: async () => {
      if (teamId && region && matchedRegion) {
        const response = await get(
          `spend/${matchedRegion.id}/team/${teamId}`,
        );
        const data = await response.json();
        // Map TeamSpendResponse to SpendInfo
        return {
          ...data,
          spend: data.total_spend ?? 0,
          max_budget: data.total_budget,
        };
      }
      // Fallback for keys without team_id
      const response = await get(`private-ai-keys/${keyId}/spend`);
      return response.json();
    },
    enabled: isLoaded && (!(teamId && region) || !!matchedRegion),
  });

  const handleLoadSpend = () => {
    setIsLoaded(true);
  };

  const handleRefreshSpend = async () => {
    setIsRefreshing(true);
    try {
      await refetch();
    } catch (error) {
      console.error("Failed to refresh spend data:", error);
    } finally {
      setIsRefreshing(false);
    }
  };

  if (!hasLiteLLMToken) {
    return null;
  }

  if (!isLoaded) {
    return (
      <Button
        variant="outline"
        size="sm"
        onClick={handleLoadSpend}
        disabled={isLoading}
      >
        {isLoading ? (
          <>
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            Loading...
          </>
        ) : (
          "Load Spend"
        )}
      </Button>
    );
  }

  if (isLoading) {
    return (
      <div className="flex items-center">
        <Loader2 className="h-4 w-4 animate-spin" />
        <span className="ml-2 text-sm">Loading spend...</span>
      </div>
    );
  }

  if (!spendData) {
    return (
      <div className="text-sm text-muted-foreground">
        Failed to load spend data
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2">
        <span className="text-sm font-medium">
          ${spendData.spend.toFixed(2)}
        </span>
        {spendData.max_budget != null && (
          <span className="text-sm text-muted-foreground">
            / ${spendData.max_budget.toFixed(2)}
          </span>
        )}
        <Button
          variant="ghost"
          size="icon"
          className="h-4 w-4"
          onClick={handleRefreshSpend}
          disabled={isRefreshing}
          aria-label="Refresh spend"
        >
          {isRefreshing ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <RefreshCw className="h-3 w-3" />
          )}
          <span className="sr-only">Refresh spend</span>
        </Button>
        {spendData.max_budget != null && (
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger>
                <Info className="h-3 w-3 text-muted-foreground" />
              </TooltipTrigger>
              <TooltipContent>
                <p>This is a shared team budget.</p>
                <p>All keys in the team share this budget.</p>
                <p>To change it, edit the team&apos;s limits.</p>
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        )}
      </div>
      {spendData.max_budget != null && (
        <span className="text-xs text-muted-foreground">
          Team budget — shared across all keys
        </span>
      )}
    </div>
  );
}
