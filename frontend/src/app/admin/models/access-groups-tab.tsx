"use client";

import { useState } from "react";
import { Loader2, Plus, Edit2, Trash2, Shield, Star, Search, Info } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { useToast } from "@/hooks/use-toast";
import { get, post, put, del } from "@/utils/api";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";

export interface AccessGroupResponse {
  id: number;
  slug: string;
  label: string;
  description: string | null;
  model_ids: number[];
  region_ids: number[];
  default_in_region_ids: number[];
  team_count: number;
  created_at: string;
  updated_at: string;
}

interface RegionOption {
  id: number;
  name: string;
}

interface ModelOption {
  id: number;
  model_id: string;
  display_name: string;
}

export default function AccessGroupsTab({ regions }: { regions: RegionOption[] }) {
  const { toast } = useToast();
  const queryClient = useQueryClient();

  const [isFormOpen, setIsFormOpen] = useState(false);
  const [editingGroup, setEditingGroup] = useState<AccessGroupResponse | null>(null);
  const [groupToDelete, setGroupToDelete] = useState<AccessGroupResponse | null>(null);

  // Form state
  const [slug, setSlug] = useState("");
  const [label, setLabel] = useState("");
  const [description, setDescription] = useState("");
  const [selectedModelIds, setSelectedModelIds] = useState<number[]>([]);
  const [selectedRegionIds, setSelectedRegionIds] = useState<number[]>([]);
  const [modelSearch, setModelSearch] = useState("");

  const { data: groups = [], isLoading } = useQuery<AccessGroupResponse[]>({
    queryKey: ["access-groups"],
    queryFn: async () => (await get("admin/access-groups")).json(),
  });

  // Shares the cache with the Catalog tab
  const { data: models = [] } = useQuery<ModelOption[]>({
    queryKey: ["admin-models"],
    queryFn: async () => (await get("admin/models")).json(),
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["access-groups"] });
    queryClient.invalidateQueries({ queryKey: ["admin-models"] });
  };

  const saveMutation = useMutation({
    mutationFn: async () => {
      const payload = {
        label,
        description: description || null,
        model_ids: selectedModelIds,
        region_ids: selectedRegionIds,
      };
      const response = editingGroup
        ? await put(`admin/access-groups/${editingGroup.id}`, payload)
        : await post("admin/access-groups", { ...payload, slug });
      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || "Failed to save access group");
      }
      return response.json();
    },
    onSuccess: () => {
      invalidate();
      setIsFormOpen(false);
      toast({
        title: "Success",
        description: `Access group ${editingGroup ? "updated" : "created"}. Model tag syncs scheduled.`,
      });
    },
    onError: (error: Error) =>
      toast({ title: "Error", description: error.message, variant: "destructive" }),
  });

  const deleteMutation = useMutation({
    mutationFn: async (id: number) => {
      const response = await del(`admin/access-groups/${id}`);
      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || "Failed to delete access group");
      }
      return response.json();
    },
    onSuccess: (data: { models_untagged: number; teams_detached: number }) => {
      invalidate();
      setGroupToDelete(null);
      toast({
        title: "Access group deleted",
        description: `${data.models_untagged} model(s) untagged, ${data.teams_detached} team(s) detached.`,
      });
    },
    onError: (error: Error) =>
      toast({ title: "Error", description: error.message, variant: "destructive" }),
  });

  const openCreate = () => {
    setEditingGroup(null);
    setSlug("");
    setLabel("");
    setDescription("");
    setSelectedModelIds([]);
    setSelectedRegionIds([]);
    setModelSearch("");
    setIsFormOpen(true);
  };

  const openEdit = (group: AccessGroupResponse) => {
    setEditingGroup(group);
    setSlug(group.slug);
    setLabel(group.label);
    setDescription(group.description || "");
    setSelectedModelIds(group.model_ids);
    setSelectedRegionIds(group.region_ids);
    setModelSearch("");
    setIsFormOpen(true);
  };

  const regionName = (id: number) => regions.find((r) => r.id === id)?.name || `#${id}`;
  const filteredModels = models.filter(
    (m) =>
      m.model_id.toLowerCase().includes(modelSearch.toLowerCase()) ||
      m.display_name.toLowerCase().includes(modelSearch.toLowerCase())
  );

  const toggleId = (id: number, setter: React.Dispatch<React.SetStateAction<number[]>>) =>
    setter((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-gray-500 max-w-2xl">
          Groups are synced to every deployed region as LiteLLM model access groups. Teams get
          their region&apos;s default group plus any opt-ins. A model in no group is unreachable
          by all teams.
        </p>
        <Button onClick={openCreate} className="flex items-center gap-2">
          <Plus className="h-4 w-4" /> Add Access Group
        </Button>
      </div>

      {isLoading ? (
        <div className="flex flex-col items-center justify-center min-h-[250px] border rounded-md space-y-4 bg-transparent">
          <Loader2 className="h-10 w-10 animate-spin text-gray-400" />
          <span className="text-sm text-gray-500">Loading access groups...</span>
        </div>
      ) : groups.length === 0 ? (
        <div className="flex flex-col items-center justify-center min-h-[250px] border border-dashed rounded-md p-8 text-center bg-transparent">
          <Shield className="h-12 w-12 text-gray-400 mb-3" />
          <h3 className="font-semibold text-lg text-gray-900">No Access Groups</h3>
          <p className="text-sm text-gray-500 max-w-sm mt-1">
            Create a default group (e.g. default-zdr) containing the models every team should
            reach, then set it as a region&apos;s default to enable enforcement.
          </p>
        </div>
      ) : (
        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-[280px]">Group</TableHead>
                <TableHead className="w-[90px]">Models</TableHead>
                <TableHead>Deployed Regions</TableHead>
                <TableHead>Default In</TableHead>
                <TableHead className="w-[90px]">Opt-in Teams</TableHead>
                <TableHead className="w-[100px] text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {groups.map((group) => {
                const isDefault = group.default_in_region_ids.length > 0;
                return (
                  <TableRow key={group.id}>
                    <TableCell>
                      <div className="flex flex-col">
                        <span className="text-sm font-semibold text-gray-900">{group.label}</span>
                        <span className="text-xs text-gray-500 font-mono mt-0.5">{group.slug}</span>
                        {group.description && (
                          <span className="text-[11px] text-gray-400 mt-1 line-clamp-1">
                            {group.description}
                          </span>
                        )}
                      </div>
                    </TableCell>
                    <TableCell className="text-sm text-gray-700">{group.model_ids.length}</TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1">
                        {group.region_ids.length === 0 ? (
                          <span className="text-xs text-gray-400 italic">Not deployed</span>
                        ) : (
                          group.region_ids.map((id) => (
                            <Badge
                              key={id}
                              variant="outline"
                              className="text-[10px] bg-transparent text-gray-600 border-gray-200"
                            >
                              {regionName(id)}
                            </Badge>
                          ))
                        )}
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1">
                        {group.default_in_region_ids.map((id) => (
                          <Badge
                            key={id}
                            className="text-[10px] bg-amber-100 text-amber-800 border-amber-200 hover:bg-amber-100 flex items-center gap-1"
                          >
                            <Star className="h-3 w-3" /> {regionName(id)}
                          </Badge>
                        ))}
                      </div>
                    </TableCell>
                    <TableCell className="text-sm text-gray-700">{group.team_count}</TableCell>
                    <TableCell className="text-right">
                      <div className="flex items-center justify-end gap-1.5">
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => openEdit(group)}
                          className="h-8 w-8 text-gray-500 hover:text-gray-900 hover:bg-gray-100"
                        >
                          <Edit2 className="h-4 w-4" />
                        </Button>
                        <TooltipProvider>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <span>
                                <Button
                                  variant="ghost"
                                  size="icon"
                                  disabled={isDefault}
                                  onClick={() => setGroupToDelete(group)}
                                  className="h-8 w-8 text-gray-500 hover:text-red-600 hover:bg-red-50"
                                >
                                  <Trash2 className="h-4 w-4" />
                                </Button>
                              </span>
                            </TooltipTrigger>
                            {isDefault && (
                              <TooltipContent>
                                Default group for {group.default_in_region_ids.map(regionName).join(", ")} —
                                change the region default first.
                              </TooltipContent>
                            )}
                          </Tooltip>
                        </TooltipProvider>
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      )}

      {/* CREATE / EDIT DIALOG */}
      <Dialog open={isFormOpen} onOpenChange={setIsFormOpen}>
        <DialogContent className="max-w-2xl max-h-[85vh] flex flex-col">
          <DialogHeader>
            <DialogTitle className="text-xl font-bold flex items-center gap-2">
              <Shield className="h-5 w-5 text-gray-700" />
              {editingGroup ? "Edit Access Group" : "Create Access Group"}
            </DialogTitle>
            <DialogDescription>
              The slug is the exact string synced to LiteLLM and cannot be changed after creation.
            </DialogDescription>
          </DialogHeader>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              saveMutation.mutate();
            }}
            className="space-y-4 flex-1 overflow-y-auto pr-1"
          >
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <label className="text-xs font-semibold text-gray-700">Slug (immutable)</label>
                <Input
                  value={slug}
                  onChange={(e) => setSlug(e.target.value)}
                  placeholder="default-zdr"
                  pattern="^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$"
                  title="Lowercase letters, digits and hyphens only"
                  required
                  disabled={!!editingGroup}
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-semibold text-gray-700">Label</label>
                <Input
                  value={label}
                  onChange={(e) => setLabel(e.target.value)}
                  placeholder="Default ZDR Models"
                  required
                />
              </div>
              <div className="space-y-1.5 col-span-2">
                <label className="text-xs font-semibold text-gray-700">Description</label>
                <Input
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="Models with zero data retention (dashboard-only, never synced)"
                />
              </div>
            </div>

            <div className="space-y-1.5 border p-3 rounded-md">
              <span className="text-sm font-semibold text-gray-900 block">Deploy to Regions</span>
              <span className="text-[11px] text-gray-500 block mb-2">
                In deployed regions the group tags its models, teams may opt in, and it can be the
                region default.
              </span>
              <div className="grid grid-cols-2 gap-2">
                {regions.map((region) => (
                  <label
                    key={region.id}
                    className="flex items-center gap-2 p-2 rounded-md border text-sm cursor-pointer"
                  >
                    <input
                      type="checkbox"
                      checked={selectedRegionIds.includes(region.id)}
                      onChange={() => toggleId(region.id, setSelectedRegionIds)}
                      className="rounded border-gray-300 h-4 w-4"
                    />
                    <span className="font-medium">{region.name}</span>
                  </label>
                ))}
              </div>
            </div>

            <div className="space-y-1.5 border p-3 rounded-md">
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm font-semibold text-gray-900">
                  Member Models ({selectedModelIds.length})
                </span>
                <div className="relative w-56">
                  <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
                  <Input
                    placeholder="Filter models..."
                    className="pl-8 h-8 text-xs"
                    value={modelSearch}
                    onChange={(e) => setModelSearch(e.target.value)}
                  />
                </div>
              </div>
              <div className="max-h-56 overflow-y-auto space-y-1">
                {filteredModels.map((model) => (
                  <label
                    key={model.id}
                    className="flex items-center gap-2 p-1.5 rounded text-sm cursor-pointer hover:bg-gray-50"
                  >
                    <input
                      type="checkbox"
                      checked={selectedModelIds.includes(model.id)}
                      onChange={() => toggleId(model.id, setSelectedModelIds)}
                      className="rounded border-gray-300 h-4 w-4"
                    />
                    <span className="font-medium">{model.display_name}</span>
                    <span className="text-xs text-gray-400 font-mono">{model.model_id}</span>
                  </label>
                ))}
                {filteredModels.length === 0 && (
                  <span className="text-xs text-gray-400 italic block p-2">No models match.</span>
                )}
              </div>
            </div>

            <DialogFooter className="pt-4 border-t border-gray-100">
              <Button type="button" variant="ghost" onClick={() => setIsFormOpen(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={saveMutation.isPending}>
                {saveMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                {editingGroup ? "Save Changes" : "Create Group"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* DELETE CONFIRMATION */}
      <Dialog open={!!groupToDelete} onOpenChange={(open) => !open && setGroupToDelete(null)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="text-xl font-bold flex items-center gap-2 text-red-600">
              <Trash2 className="h-5 w-5" /> Delete Access Group
            </DialogTitle>
            <DialogDescription>
              Member models lose this tag and opted-in teams are detached across all deployed
              regions.
            </DialogDescription>
          </DialogHeader>
          {groupToDelete && (
            <div className="bg-gray-50 border rounded-lg p-3 space-y-1.5">
              <div className="text-sm font-bold text-gray-900">{groupToDelete.label}</div>
              <div className="text-xs text-gray-500 font-mono">{groupToDelete.slug}</div>
              <div className="text-xs text-red-600 pt-1 flex items-center gap-1">
                <Info className="h-3 w-3" />
                {groupToDelete.model_ids.length} model(s) will be untagged,{" "}
                {groupToDelete.team_count} team(s) detached.
              </div>
            </div>
          )}
          <DialogFooter className="pt-4 border-t border-gray-100">
            <Button type="button" variant="ghost" onClick={() => setGroupToDelete(null)}>
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              onClick={() => groupToDelete && deleteMutation.mutate(groupToDelete.id)}
              disabled={deleteMutation.isPending}
            >
              {deleteMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Delete Group
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
