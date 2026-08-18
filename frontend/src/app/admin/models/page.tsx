"use client";

import {
  Loader2,
  Cpu,
  Search,
  CheckCircle2,
  AlertCircle,
  Layers,
  Globe2,
  Shield,
  AlertTriangle,
  GitBranch,
} from "lucide-react";
import { useState, useMemo } from "react";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { get } from "@/utils/api";
import { useQuery } from "@tanstack/react-query";
import AccessGroupsTab from "./access-groups-tab";

interface AdminModelRegionResponse {
  region_id: number;
  region_name: string;
  is_active: boolean;
  sync_status: "pending" | "synced" | "failed" | "not_configured";
  sync_error: string | null;
  synced_at: string | null;
  litellm_params_override?: Record<string, any> | null;
}

interface AdminModelAliasTarget {
  region_id: number;
  target_model_id: number;
}

interface AdminModelResponse {
  id: number;
  model_id: string;
  display_name: string;
  provider: string;
  type: string;
  context_length: number | null;
  max_output_tokens: number | null;
  description: string | null;
  real_eol: string | null;
  override_eol: string | null;
  is_active_globally: boolean;
  litellm_params: Record<string, any> | null;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
  regions: AdminModelRegionResponse[];
  access_group_ids: number[];
  access_group_slugs: string[];
  is_alias?: boolean;
  alias_targets?: AdminModelAliasTarget[];
}

// The catalog is authored in the amazeeai-model-catalog repo and applied via
// POST /admin/models/apply — this page is a read-only dashboard for inventory
// and per-region sync status.
export default function ModelsPage() {
  const [searchTerm, setSearchTerm] = useState("");
  const [selectedProvider, setSelectedProvider] = useState("all");
  const [activeTab, setActiveTab] = useState("catalog");

  const { data: models = [], isLoading } = useQuery<AdminModelResponse[]>({
    queryKey: ["admin-models"],
    queryFn: async () => (await get("admin/models")).json(),
  });

  // Extract unique providers for filter
  const providers = useMemo(() => {
    const list = new Set(models.map((m) => m.provider.toLowerCase()));
    return ["all", ...Array.from(list)];
  }, [models]);

  // Fetch regions from authoritative admin endpoint
  const { data: adminRegions = [] } = useQuery<any[]>({
    queryKey: ["admin-regions"],
    queryFn: async () => {
      const response = await get("regions/admin");
      return response.json();
    },
  });

  // Unique list of regions from authoritative source (with models[0] fallback)
  const regions = useMemo(() => {
    if (adminRegions.length > 0) {
      return adminRegions
        .filter((r) => r.is_active)
        .map((r) => ({
          id: Number(r.id),
          name: r.name,
          regional_area: (r.regional_area as string | null) ?? null,
        }));
    }
    if (models.length === 0) return [];
    // Grab from the first model which returns active regions as a fallback
    return models[0].regions.map((r) => ({
      id: r.region_id,
      name: r.region_name,
      regional_area: null as string | null,
    }));
  }, [adminRegions, models]);

  // Filtered models list
  const filteredModels = useMemo(() => {
    return models.filter((model) => {
      const matchesSearch =
        model.model_id.toLowerCase().includes(searchTerm.toLowerCase()) ||
        model.display_name.toLowerCase().includes(searchTerm.toLowerCase());
      const matchesProvider =
        selectedProvider === "all" || model.provider.toLowerCase() === selectedProvider;
      return matchesSearch && matchesProvider;
    });
  }, [models, searchTerm, selectedProvider]);

  const getEolBadge = (model: AdminModelResponse) => {
    const activeEolStr = model.override_eol || model.real_eol;
    if (!activeEolStr) {
      return (
        <Badge variant="outline" className="bg-transparent text-gray-400 border-gray-200 hover:bg-transparent">
          No EOL Set
        </Badge>
      );
    }

    const activeEol = new Date(activeEolStr);
    const now = new Date();
    const isPast = activeEol < now;

    const formattedDate = activeEol.toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });

    if (isPast) {
      return (
        <div className="flex flex-col gap-1 items-start">
          <Badge variant="outline" className="bg-gray-100 text-gray-600 border-gray-300 hover:bg-gray-100">
            Deprecated
          </Badge>
          <span className="text-[10px] text-muted-foreground font-medium">Ended {formattedDate}</span>
        </div>
      );
    }

    const diffTime = Math.abs(activeEol.getTime() - now.getTime());
    const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));
    const isWarning = diffDays <= 90;

    if (model.override_eol) {
      return (
        <div className="flex flex-col gap-1 items-start">
          <Badge variant="outline" className="bg-transparent text-gray-700 border-gray-300 hover:bg-transparent">
            Override Deprecating
          </Badge>
          <span className="text-[10px] text-muted-foreground font-medium">
            {isWarning ? `In ${diffDays} days (${formattedDate})` : formattedDate}
          </span>
        </div>
      );
    }

    return (
      <div className="flex flex-col gap-1 items-start">
        <Badge variant="outline" className="bg-transparent text-gray-700 border-gray-300 hover:bg-transparent">
          EOL Scheduled
        </Badge>
        <span className="text-[10px] text-muted-foreground font-medium">
          {isWarning ? `In ${diffDays} days (${formattedDate})` : formattedDate}
        </span>
      </div>
    );
  };

  const getSyncStatusBadge = (region: AdminModelRegionResponse) => {
    if (!region.is_active) {
      if (region.sync_status === "pending") {
        return (
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <div className="flex items-center gap-1 bg-gray-100 text-gray-700 border border-gray-200 rounded-full px-2 py-0.5 text-xs">
                  <Loader2 className="h-3 w-3 animate-spin text-gray-500" />
                  Deactivating
                </div>
              </TooltipTrigger>
              <TooltipContent>Deregistering model from regional proxy...</TooltipContent>
            </Tooltip>
          </TooltipProvider>
        );
      }
      if (region.sync_status === "failed") {
        return (
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <div className="flex items-center gap-1 bg-gray-100 text-red-700 border border-gray-200 rounded-full px-2 py-0.5 text-xs cursor-help">
                  <AlertCircle className="h-3 w-3 text-red-600" />
                  Failed Off
                </div>
              </TooltipTrigger>
              <TooltipContent className="max-w-xs break-words">
                <span className="font-bold">Deregistration Failed:</span>
                <p className="text-xs mt-1">{region.sync_error || "Unknown LiteLLM connection error"}</p>
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        );
      }
      return (
        <span className="text-gray-400 text-xs flex items-center gap-1 px-2 py-0.5">
          Inactive
        </span>
      );
    }

    switch (region.sync_status) {
      case "synced":
        return (
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <div className="flex items-center gap-1 bg-gray-100 text-green-700 border border-gray-200 rounded-full px-2 py-0.5 text-xs">
                  <CheckCircle2 className="h-3 w-3 text-green-600" />
                  Synced
                </div>
              </TooltipTrigger>
              <TooltipContent>
                Model successfully loaded and verified in LiteLLM.
                {region.synced_at && (
                  <p className="text-[10px] text-gray-500 mt-1">
                    Last sync: {new Date(region.synced_at).toLocaleString()}
                  </p>
                )}
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        );
      case "pending":
        return (
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <div className="flex items-center gap-1 bg-gray-100 text-gray-700 border border-gray-200 rounded-full px-2 py-0.5 text-xs">
                  <Loader2 className="h-3 w-3 animate-spin text-gray-500" />
                  Pending
                </div>
              </TooltipTrigger>
              <TooltipContent>Deploying and configuring model on region proxy...</TooltipContent>
            </Tooltip>
          </TooltipProvider>
        );
      case "failed":
        return (
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <div className="flex items-center gap-1 bg-gray-100 text-red-700 border border-gray-200 rounded-full px-2 py-0.5 text-xs cursor-help">
                  <AlertCircle className="h-3 w-3 text-red-600" />
                  Failed
                </div>
              </TooltipTrigger>
              <TooltipContent className="max-w-xs break-words">
                <span className="font-bold">Sync Error:</span>
                <p className="text-xs mt-1">{region.sync_error || "Unknown LiteLLM connection error"}</p>
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        );
      case "not_configured":
      default:
        return (
          <span className="text-gray-400 text-xs flex items-center gap-1 px-2 py-0.5">
            Not Configured
          </span>
        );
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-3xl font-bold">Models</h1>
        <a
          href="https://github.com/amazeeio/amazeeai-model-catalog"
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-1.5 text-xs text-gray-500 hover:text-gray-900 border border-gray-200 rounded-md px-3 py-1.5"
        >
          <GitBranch className="h-3.5 w-3.5" />
          Managed via amazeeai-model-catalog — changes go through PRs there
        </a>
      </div>

      <Tabs value={activeTab} onValueChange={setActiveTab} className="space-y-4">
        <div className="flex flex-col gap-4 md:flex-row md:items-center justify-between border rounded-md p-4 bg-transparent">
          <div className="flex flex-1 flex-col gap-3 md:flex-row md:items-center">
            <div className="relative flex-1 max-w-sm">
              <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input
                placeholder="Search by ID or Display Name..."
                className="pl-9 bg-transparent"
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
              />
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground">Provider:</span>
              <div className="flex flex-wrap gap-1">
                {providers.map((p) => (
                  <button
                    key={p}
                    onClick={() => setSelectedProvider(p)}
                    className={`px-3 py-1 rounded-md text-xs font-semibold capitalize transition-all border ${
                      selectedProvider === p
                        ? "bg-gray-900 text-white border-gray-900"
                        : "bg-transparent text-gray-600 border-gray-200 hover:bg-gray-50"
                    }`}
                  >
                    {p === "all" ? "All Providers" : p}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <TabsList className="w-full md:w-auto">
            <TabsTrigger value="catalog" className="flex items-center gap-1.5">
              <Layers className="h-4 w-4" /> Catalog
            </TabsTrigger>
            <TabsTrigger value="matrix" className="flex items-center gap-1.5">
              <Globe2 className="h-4 w-4" /> Regions Matrix
            </TabsTrigger>
            <TabsTrigger value="access-groups" className="flex items-center gap-1.5">
              <Shield className="h-4 w-4" /> Access Groups
            </TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="access-groups" className="mt-0">
          <AccessGroupsTab regions={regions} />
        </TabsContent>

        {activeTab === "access-groups" ? null : isLoading ? (
          <div className="flex flex-col items-center justify-center min-h-[300px] border rounded-md space-y-4 bg-transparent">
            <Loader2 className="h-10 w-10 animate-spin text-gray-400" />
            <span className="text-sm text-gray-500">Loading catalog matrix...</span>
          </div>
        ) : (
          <>
            {filteredModels.length === 0 ? (
              <div className="flex flex-col items-center justify-center min-h-[250px] border border-dashed rounded-md p-8 text-center bg-transparent">
                <Cpu className="h-12 w-12 text-gray-400 mb-3" />
                <h3 className="font-semibold text-lg text-gray-900">No Models Found</h3>
                <p className="text-sm text-gray-500 max-w-sm mt-1">
                  No models matched the specified search queries or filters. Try adjusting your search.
                </p>
              </div>
            ) : (
              <>
                <TabsContent value="catalog" className="mt-0 space-y-4">
                  <div className="rounded-md border">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead className="w-[280px]">Model</TableHead>
                          <TableHead className="w-[120px]">Provider</TableHead>
                          <TableHead className="w-[100px]">Type</TableHead>
                          <TableHead className="w-[110px]">Context Window</TableHead>
                          <TableHead className="w-[160px]">Access Groups</TableHead>
                          <TableHead className="w-[130px]">Deprecation / EOL</TableHead>
                          <TableHead className="w-[100px]">Global Status</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {filteredModels.map((model) => (
                          <TableRow key={model.id}>
                            <TableCell className="font-medium">
                              <div className="flex flex-col">
                                <span className="text-sm font-semibold flex items-center gap-1.5 text-gray-900">
                                  {model.display_name}
                                </span>
                                <span className="text-xs text-gray-500 font-mono mt-0.5">
                                  {model.model_id}
                                </span>
                                {model.description && (
                                  <span className="text-[11px] text-gray-400 mt-1 line-clamp-1">
                                    {model.description}
                                  </span>
                                )}
                              </div>
                            </TableCell>
                            <TableCell className="capitalize font-medium text-gray-700">
                              {model.provider}
                            </TableCell>
                            <TableCell>
                              <div className="flex flex-col gap-1 items-start">
                                <Badge variant="outline" className="capitalize font-mono text-[10px] bg-transparent text-gray-600 border-gray-200 hover:bg-transparent">
                                  {model.type}
                                </Badge>
                                {model.is_alias && (
                                  <Badge className="text-[10px] bg-blue-100 text-blue-800 border-blue-200 hover:bg-blue-100">
                                    alias
                                  </Badge>
                                )}
                              </div>
                            </TableCell>
                            <TableCell className="text-gray-700 text-sm">
                              {model.context_length ? (
                                <div className="flex flex-col">
                                  <span>{model.context_length.toLocaleString()}</span>
                                  <span className="text-[10px] text-gray-400 font-mono">
                                    out: {model.max_output_tokens?.toLocaleString() || "N/A"}
                                  </span>
                                </div>
                              ) : (
                                <span className="text-gray-400">-</span>
                              )}
                            </TableCell>
                            <TableCell>
                              {model.access_group_slugs?.length ? (
                                <div className="flex flex-wrap gap-1">
                                  {model.access_group_slugs.map((slug) => (
                                    <Badge
                                      key={slug}
                                      variant="outline"
                                      className="text-[10px] font-mono bg-transparent text-gray-600 border-gray-200 hover:bg-transparent"
                                    >
                                      {slug}
                                    </Badge>
                                  ))}
                                </div>
                              ) : (
                                <TooltipProvider>
                                  <Tooltip>
                                    <TooltipTrigger asChild>
                                      <div className="flex items-center gap-1 text-amber-700 text-xs cursor-help">
                                        <AlertTriangle className="h-3.5 w-3.5" />
                                        No group
                                      </div>
                                    </TooltipTrigger>
                                    <TooltipContent className="max-w-xs">
                                      Not in any access group — unreachable by all teams in
                                      regions with enforcement enabled.
                                    </TooltipContent>
                                  </Tooltip>
                                </TooltipProvider>
                              )}
                            </TableCell>
                            <TableCell>{getEolBadge(model)}</TableCell>
                            <TableCell>
                              <span
                                className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                                  model.is_active_globally
                                    ? "bg-green-100 text-green-800"
                                    : "bg-red-100 text-red-800"
                                }`}
                              >
                                {model.is_active_globally ? "Active" : "Inactive"}
                              </span>
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                </TabsContent>

                <TabsContent value="matrix" className="mt-0">
                  <div className="rounded-md border">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead className="w-[300px]">Model (Inventory)</TableHead>
                          <TableHead className="w-[120px]">Global Status</TableHead>
                          {regions.map((region) => (
                            <TableHead key={region.id} className="text-center w-[160px]">
                              {region.name}
                            </TableHead>
                          ))}
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {filteredModels.map((model) => (
                          <TableRow key={model.id}>
                            <TableCell className="font-medium">
                              <div className="flex flex-col">
                                <span className="text-sm font-bold text-gray-900">
                                  {model.display_name}
                                </span>
                                <span className="text-xs text-gray-500 font-mono mt-0.5">
                                  {model.model_id}
                                </span>
                              </div>
                            </TableCell>
                            <TableCell>
                              <span
                                className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                                  model.is_active_globally
                                    ? "bg-green-100 text-green-800"
                                    : "bg-red-100 text-red-800"
                                }`}
                              >
                                {model.is_active_globally ? "Active" : "Inactive"}
                              </span>
                            </TableCell>
                            {regions.map((region) => {
                              const assoc = model.regions.find((r) => r.region_id === region.id) || {
                                region_id: region.id,
                                region_name: region.name,
                                is_active: false,
                                sync_status: "not_configured" as const,
                                sync_error: null,
                                synced_at: null,
                              };
                              return (
                                <TableCell key={region.id} className="text-center border-l border-gray-100">
                                  <div className="flex flex-col items-center gap-2">
                                    {getSyncStatusBadge(assoc as AdminModelRegionResponse)}
                                  </div>
                                </TableCell>
                              );
                            })}
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                </TabsContent>
              </>
            )}
          </>
        )}
      </Tabs>
    </div>
  );
}
