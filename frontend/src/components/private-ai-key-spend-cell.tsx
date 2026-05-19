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
}

export function PrivateAIKeySpendCell({
  keyId,
  hasLiteLLMToken,
}: PrivateAIKeySpendCellProps) {
  const [isLoaded, setIsLoaded] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);

  // Query for spend data - only enabled when isLoaded is true
  const {
    data: spendData,
    isLoading,
    refetch,
  } = useQuery<SpendInfo>({
    queryKey: ["private-ai-key-spend", keyId],
    queryFn: async () => {
      const response = await get(`private-ai-keys/${keyId}/spend`);
      return response.json();
    },
    enabled: isLoaded,
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
      >
        {isRefreshing ? (
          <Loader2 className="h-3 w-3 animate-spin" />
        ) : (
          <RefreshCw className="h-3 w-3" />
        )}
      </Button>
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <Info className="h-3 w-3 text-muted-foreground" />
          </TooltipTrigger>
          <TooltipContent>
            <p>Shown budget is this key&apos;s cap.</p>
            <p>Team-level budgets may override key caps.</p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    </div>
  );
}
