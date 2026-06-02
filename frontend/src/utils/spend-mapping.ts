import { SpendInfo } from "@/types/spend";

/**
 * Maps a team spend response (TeamSpendResponse) to the SpendInfo interface.
 * TeamSpendResponse uses total_spend and total_budget, while SpendInfo
 * uses spend and max_budget.
 */
export function mapTeamSpendToSpendInfo(data: any): SpendInfo {
  return {
    ...data,
    spend: data.total_spend ?? 0,
    max_budget: data.total_budget ?? null,
  };
}
