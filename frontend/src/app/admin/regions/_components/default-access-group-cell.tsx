"use client";

import { useState } from "react";
import { Loader2, RefreshCw, ShieldAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useToast } from "@/hooks/use-toast";
import { Region } from "@/types/region";
import { get, post, put } from "@/utils/api";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";

interface AccessGroupSummary {
  id: number;
  slug: string;
  label: string;
  region_ids: number[];
}

interface TeamGroupSyncRun {
  id: number;
  region_id: number;
  status: "running" | "done" | "failed";
  total: number;
  done: number;
  failed_team_ids: number[] | null;
  error_sample: string | null;
  started_at: string;
  finished_at: string | null;
}

/**
 * Per-region control for the access-group enforcement switch: a dropdown of
 * groups deployed to the region (plus "None — all models") with a confirmation
 * dialog, and live progress of the latest team fan-out run.
 */
export function DefaultAccessGroupCell({ region }: { region: Region }) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [pendingGroupId, setPendingGroupId] = useState<number | null | undefined>(undefined);

  const { data: groups = [] } = useQuery<AccessGroupSummary[]>({
    queryKey: ["access-groups"],
    queryFn: async () => (await get("admin/access-groups")).json(),
  });

  const { data: latestRun } = useQuery<TeamGroupSyncRun | null>({
    queryKey: ["team-group-sync-run", region.id],
    queryFn: async () => {
      const response = await get(`admin/regions/${region.id}/team-group-sync-run`);
      if (!response.ok) return null;
      const text = await response.text();
      return text && text !== "null" ? JSON.parse(text) : null;
    },
    refetchInterval: (query) => (query.state.data?.status === "running" ? 3000 : false),
  });

  const setDefaultMutation = useMutation({
    mutationFn: async (groupId: number | null) => {
      const response = await put(`admin/regions/${region.id}/default-access-group`, {
        group_id: groupId,
      });
      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || "Failed to update default access group");
      }
      return response.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["regions"] });
      queryClient.invalidateQueries({ queryKey: ["access-groups"] });
      queryClient.invalidateQueries({ queryKey: ["team-group-sync-run", region.id] });
      setPendingGroupId(undefined);
      toast({
        title: "Default access group updated",
        description: "Team sync fan-out started for this region.",
      });
    },
    onError: (error: Error) => {
      setPendingGroupId(undefined);
      toast({ title: "Error", description: error.message, variant: "destructive" });
    },
  });

  const retryMutation = useMutation({
    mutationFn: async () => {
      const response = await post(`admin/regions/${region.id}/team-group-sync-run`, {});
      if (!response.ok) throw new Error("Failed to start team sync run");
      return response.json();
    },
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["team-group-sync-run", region.id] }),
    onError: (error: Error) =>
      toast({ title: "Error", description: error.message, variant: "destructive" }),
  });

  const deployedGroups = groups.filter((g) => g.region_ids.includes(region.id));
  const currentId = region.default_access_group_id ?? null;
  const pendingGroup = groups.find((g) => g.id === pendingGroupId);

  return (
    <div className="flex flex-col gap-1 min-w-[180px]">
      <select
        value={currentId === null ? "" : String(currentId)}
        onChange={(e) => setPendingGroupId(e.target.value === "" ? null : Number(e.target.value))}
        className="flex h-8 rounded-md border border-input bg-background px-2 py-1 text-xs text-gray-900 focus-visible:outline-none"
      >
        <option value="">None — all models</option>
        {deployedGroups.map((g) => (
          <option key={g.id} value={String(g.id)}>
            {g.label} ({g.slug})
          </option>
        ))}
      </select>

      {latestRun && (
        <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          {latestRun.status === "running" && (
            <Loader2 className="h-3 w-3 animate-spin text-gray-500" />
          )}
          <span className={latestRun.status === "failed" ? "text-red-600" : undefined}>
            Teams: {latestRun.done}/{latestRun.total}
            {latestRun.failed_team_ids?.length
              ? ` · ${latestRun.failed_team_ids.length} failed`
              : latestRun.status === "done"
                ? " · synced"
                : ""}
          </span>
          {latestRun.status === "failed" && (
            <Button
              variant="ghost"
              size="sm"
              className="h-5 px-1.5 text-[10px] text-gray-500"
              disabled={retryMutation.isPending}
              onClick={() => retryMutation.mutate()}
            >
              <RefreshCw className="h-3 w-3 mr-1" /> Retry
            </Button>
          )}
        </div>
      )}

      {/* CONFIRMATION — this is the enforcement switch, spell out the fan-out */}
      <Dialog
        open={pendingGroupId !== undefined}
        onOpenChange={(open) => !open && setPendingGroupId(undefined)}
      >
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="text-xl font-bold flex items-center gap-2">
              <ShieldAlert className="h-5 w-5 text-amber-600" />
              Change access-group enforcement
            </DialogTitle>
            <DialogDescription>
              {pendingGroupId === null
                ? `Turn enforcement OFF for '${region.name}': every team's model restriction is removed (legacy all-models behavior).`
                : `Every team in '${region.name}' will be restricted to '${pendingGroup?.label}' plus their opt-in groups.`}{" "}
              This rewrites the models list of all teams in this region on LiteLLM.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="pt-4 border-t border-gray-100">
            <Button type="button" variant="ghost" onClick={() => setPendingGroupId(undefined)}>
              Cancel
            </Button>
            <Button
              type="button"
              onClick={() =>
                pendingGroupId !== undefined && setDefaultMutation.mutate(pendingGroupId)
              }
              disabled={setDefaultMutation.isPending}
            >
              {setDefaultMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Confirm & Start Fan-out
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
