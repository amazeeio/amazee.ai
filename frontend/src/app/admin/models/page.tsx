"use client";

import {
  Loader2,
  Cpu,
  Plus,
  Search,
  Edit2,
  Trash2,
  CheckCircle2,
  AlertCircle,
  HelpCircle,
  RefreshCw,
  Layers,
  Globe2,
  Info,
} from "lucide-react";
import { useState, useMemo } from "react";
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
import { Switch } from "@/components/ui/switch";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useToast } from "@/hooks/use-toast";
import { get, post, put, del } from "@/utils/api";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";

interface AdminModelRegionResponse {
  region_id: number;
  region_name: string;
  is_active: boolean;
  sync_status: "pending" | "synced" | "failed" | "not_configured";
  sync_error: string | null;
  synced_at: string | null;
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
}

export default function ModelsPage() {
  const { toast } = useToast();
  const queryClient = useQueryClient();

  // Search & Filter state
  const [searchTerm, setSearchTerm] = useState("");
  const [selectedProvider, setSelectedProvider] = useState("all");
  const [activeTab, setActiveTab] = useState("catalog");

  // Dialog states
  const [isFormOpen, setIsFormOpen] = useState(false);
  const [editingModel, setEditingRegionModel] = useState<AdminModelResponse | null>(null);
  const [isDeleteOpen, setIsDeleteOpen] = useState(false);
  const [modelToDelete, setModelToDelete] = useState<AdminModelResponse | null>(null);

  // Form states
  const [modelId, setModelId] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [provider, setProvider] = useState("");
  const [type, setType] = useState("chat");
  const [contextLength, setContextLength] = useState("");
  const [maxOutputTokens, setMaxOutputTokens] = useState("");
  const [description, setDescription] = useState("");
  const [realEol, setRealEol] = useState("");
  const [overrideEol, setOverrideEol] = useState("");
  const [isActiveGlobally, setIsActiveGlobally] = useState(true);
  const [litellmParams, setLitellmParams] = useState("{}");
  const [selectedRegionIds, setSelectedRegionIds] = useState<number[]>([]);
  // When set, the form was opened via "Prep Import": submit creates the model
  // via /admin/models/import (marks the source region already-synced, no sync task).
  const [importSourceRegionId, setImportSourceRegionId] = useState<number | null>(null);

  // Import Dialog states
  const [isImportOpen, setIsImportOpen] = useState(false);
  const [importRegionId, setImportRegionId] = useState<string>("");
  const [importableModels, setImportableModels] = useState<any[]>([]);
  const [isImportableLoading, setIsImportableLoading] = useState(false);

  // Fetch models
  const { data: models = [], isLoading } = useQuery<AdminModelResponse[]>({
    queryKey: ["admin-models"],
    queryFn: async () => {
      const response = await get("admin/models");
      return response.json();
    },
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
        .map((r) => ({ id: Number(r.id), name: r.name }));
    }
    if (models.length === 0) return [];
    // Grab from the first model which returns active regions as a fallback
    return models[0].regions.map((r) => ({ id: r.region_id, name: r.region_name }));
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

  // Fetch importable models on demand
  const fetchImportableModels = async (regionId: string) => {
    if (!regionId) {
      setImportableModels([]);
      return;
    }
    setIsImportableLoading(true);
    try {
      const response = await get(`admin/models/importable?region_id=${regionId}`);
      if (response.ok) {
        const data = await response.json();
        setImportableModels(data);
      } else {
        const errData = await response.json();
        toast({
          title: "Import Error",
          description: errData.detail || "Failed to load importable models.",
          variant: "destructive",
        });
      }
    } catch (err: any) {
      toast({
        title: "Import Error",
        description: err.message || "Failed to communicate with regional LiteLLM proxy.",
        variant: "destructive",
      });
    } finally {
      setIsImportableLoading(false);
    }
  };

  // Create or Update Mutation
  const saveModelMutation = useMutation({
    mutationFn: async (payload: any) => {
      if (editingModel) {
        const response = await put(`admin/models/${editingModel.id}`, payload);
        return response.json();
      } else if (importSourceRegionId !== null) {
        // Model already exists in this region's LiteLLM; import marks it synced
        // without re-pushing config (avoids overwriting the live model).
        // litellm_params are fetched server-side from the region proxy — never sent.
        const { litellm_params: _ignored, ...importPayload } = payload;
        const response = await post("admin/models/import", {
          ...importPayload,
          region_id: importSourceRegionId,
        });
        return response.json();
      } else {
        const response = await post("admin/models", payload);
        return response.json();
      }
    },
    onSuccess: async (savedModel) => {
      try {
        // Compute and trigger any region changes (adding/removing active regions)
        for (const region of regions) {
          // The import source region is already synced by /import; don't re-toggle it.
          if (region.id === importSourceRegionId) continue;
          const wasActive = editingModel
            ? (editingModel.regions.find((r) => r.region_id === region.id)?.is_active ?? false)
            : false;
          const wantsActive = selectedRegionIds.includes(region.id);
          if (wasActive !== wantsActive) {
            await post("admin/models/region-toggle", {
              model_id: savedModel.id,
              region_id: region.id,
              is_active: wantsActive,
            });
          }
        }
      } catch (err: any) {
        toast({
          title: "Regional Rollout Error",
          description: "Model saved, but some regional rollouts failed: " + err.message,
          variant: "destructive",
        });
      }

      queryClient.invalidateQueries({ queryKey: ["admin-models"] });
      setIsFormOpen(false);
      resetForm();
      toast({
        title: "Success",
        description: `Model ${editingModel ? "updated" : "created"} successfully`,
      });
    },
    onError: (error: Error) => {
      toast({
        title: "Error",
        description: error.message,
        variant: "destructive",
      });
    },
  });

  // Delete Mutation
  const deleteModelMutation = useMutation({
    mutationFn: async (id: number) => {
      await del(`admin/models/${id}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-models"] });
      setIsDeleteOpen(false);
      setModelToDelete(null);
      toast({
        title: "Success",
        description: "Model soft-deleted from inventory successfully",
      });
    },
    onError: (error: Error) => {
      toast({
        title: "Error",
        description: error.message,
        variant: "destructive",
      });
    },
  });

  // Region Toggle Mutation
  const toggleRegionMutation = useMutation({
    mutationFn: async (variables: { modelId: number; regionId: number; isActive: boolean }) => {
      const response = await post("admin/models/region-toggle", {
        model_id: variables.modelId,
        region_id: variables.regionId,
        is_active: variables.isActive,
      });
      return response.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-models"] });
      toast({
        title: "Syncing",
        description: "Regional alignment triggered in background.",
      });
    },
    onError: (error: Error) => {
      toast({
        title: "Sync Error",
        description: error.message,
        variant: "destructive",
      });
    },
  });

  const resetForm = () => {
    setEditingRegionModel(null);
    setModelId("");
    setDisplayName("");
    setProvider("");
    setType("chat");
    setContextLength("");
    setMaxOutputTokens("");
    setDescription("");
    setRealEol("");
    setOverrideEol("");
    setIsActiveGlobally(true);
    setLitellmParams("{}");
    setSelectedRegionIds([]);
    setImportSourceRegionId(null);
  };

  const handleOpenCreate = () => {
    resetForm();
    setIsFormOpen(true);
  };

  const handleOpenEdit = async (model: AdminModelResponse) => {
    try {
      const response = await get(`admin/models/${model.id}`);
      const fullModel: AdminModelResponse = await response.json();
      
      setImportSourceRegionId(null);
      setEditingRegionModel(fullModel);
      setModelId(fullModel.model_id);
      setDisplayName(fullModel.display_name);
      setProvider(fullModel.provider);
      setType(fullModel.type);
      setContextLength(fullModel.context_length ? String(fullModel.context_length) : "");
      setMaxOutputTokens(fullModel.max_output_tokens ? String(fullModel.max_output_tokens) : "");
      setDescription(fullModel.description || "");
      
      // Format EOL strings for input values (YYYY-MM-DD)
      if (fullModel.real_eol) {
        setRealEol(new Date(fullModel.real_eol).toISOString().substring(0, 10));
      } else {
        setRealEol("");
      }
      if (fullModel.override_eol) {
        setOverrideEol(new Date(fullModel.override_eol).toISOString().substring(0, 10));
      } else {
        setOverrideEol("");
      }
      
      setIsActiveGlobally(fullModel.is_active_globally);
      setLitellmParams(fullModel.litellm_params ? JSON.stringify(fullModel.litellm_params, null, 2) : "{}");
      
      const activeRegionIds = fullModel.regions
        .filter((r) => r.is_active)
        .map((r) => r.region_id);
      setSelectedRegionIds(activeRegionIds);
      
      setIsFormOpen(true);
    } catch (err: any) {
      toast({
        title: "Error Loading Model",
        description: err.message || "Failed to load model details for editing.",
        variant: "destructive",
      });
    }
  };

  const handleOpenDelete = (model: AdminModelResponse) => {
    setModelToDelete(model);
    setIsDeleteOpen(true);
  };

  const handleFormSubmit = (e: React.FormEvent) => {
    e.preventDefault();

    // Validate date logic
    if (overrideEol && realEol && new Date(overrideEol) > new Date(realEol)) {
      toast({
        title: "Validation Error",
        description: "Override EOL cannot be set after Real EOL date.",
        variant: "destructive",
      });
      return;
    }

    // Validate JSON params
    let parsedParams = {};
    try {
      parsedParams = JSON.parse(litellmParams);
    } catch (err) {
      toast({
        title: "JSON Parsing Error",
        description: "LiteLLM Parameters must be a valid JSON object.",
        variant: "destructive",
      });
      return;
    }

    const payload = {
      model_id: modelId,
      display_name: displayName,
      provider: provider,
      type: type,
      context_length: contextLength ? parseInt(contextLength) : null,
      max_output_tokens: maxOutputTokens ? parseInt(maxOutputTokens) : null,
      description: description || null,
      real_eol: realEol ? new Date(realEol).toISOString() : null,
      override_eol: overrideEol ? new Date(overrideEol).toISOString() : null,
      is_active_globally: isActiveGlobally,
      litellm_params: parsedParams,
    };

    saveModelMutation.mutate(payload);
  };

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
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            onClick={() => {
              setImportRegionId("");
              setImportableModels([]);
              setIsImportOpen(true);
            }}
            className="flex items-center gap-2 bg-transparent text-gray-700 border-gray-200 hover:bg-gray-50"
          >
            <RefreshCw className="h-4 w-4" /> Import from Region
          </Button>
          <Button onClick={handleOpenCreate} className="flex items-center gap-2">
            <Plus className="h-4 w-4" /> Add Model
          </Button>
        </div>
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
          </TabsList>
        </div>

      {isLoading ? (
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
                          <TableHead className="w-[130px]">Deprecation / EOL</TableHead>
                          <TableHead className="w-[100px]">Global Status</TableHead>
                          <TableHead className="w-[100px] text-right">Actions</TableHead>
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
                              <Badge variant="outline" className="capitalize font-mono text-[10px] bg-transparent text-gray-600 border-gray-200 hover:bg-transparent">
                                {model.type}
                              </Badge>
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
                            <TableCell className="text-right">
                              <div className="flex items-center justify-end gap-1.5">
                                <Button
                                  variant="ghost"
                                  size="icon"
                                  onClick={() => handleOpenEdit(model)}
                                  className="h-8 w-8 text-gray-500 hover:text-gray-900 hover:bg-gray-100"
                                >
                                  <Edit2 className="h-4 w-4" />
                                </Button>
                                <Button
                                  variant="ghost"
                                  size="icon"
                                  onClick={() => handleOpenDelete(model)}
                                  className="h-8 w-8 text-gray-500 hover:text-red-600 hover:bg-red-50"
                                >
                                  <Trash2 className="h-4 w-4" />
                                </Button>
                              </div>
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

                              const isSyncing = assoc.sync_status === "pending" || (
                                toggleRegionMutation.isPending &&
                                toggleRegionMutation.variables?.modelId === model.id &&
                                toggleRegionMutation.variables?.regionId === region.id
                              );

                              return (
                                <TableCell key={region.id} className="text-center border-l border-gray-100">
                                  <div className="flex flex-col items-center gap-2">
                                    <Switch
                                      checked={assoc.is_active}
                                      disabled={isSyncing || !model.is_active_globally}
                                      onCheckedChange={(checked) => {
                                        toggleRegionMutation.mutate({
                                          modelId: model.id,
                                          regionId: region.id,
                                          isActive: checked,
                                        });
                                      }}
                                    />
                                    {getSyncStatusBadge(assoc as AdminModelRegionResponse)}
                                    {(assoc.sync_status === "failed" || assoc.sync_status === "pending") && (
                                      // Re-toggle with the current state: resets the row to
                                      // 'pending' and queues a fresh sync task. Covers failed
                                      // syncs and 'pending' rows stranded by an app restart.
                                      <Button
                                        variant="ghost"
                                        size="sm"
                                        className="h-5 px-1.5 text-[10px] text-gray-500"
                                        disabled={toggleRegionMutation.isPending}
                                        onClick={() =>
                                          toggleRegionMutation.mutate({
                                            modelId: model.id,
                                            regionId: region.id,
                                            // A globally-inactive model can only retry deregistration:
                                            // the backend rejects enabling regions for it.
                                            isActive: assoc.is_active && model.is_active_globally,
                                          })
                                        }
                                      >
                                        <RefreshCw className="h-3 w-3 mr-1" /> Retry
                                      </Button>
                                    )}
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

      {/* CREATE & EDIT MODEL DIALOG */}
      <Dialog open={isFormOpen} onOpenChange={setIsFormOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle className="text-xl font-bold flex items-center gap-2">
              <Cpu className="h-5 w-5 text-gray-700" />
              {editingModel ? "Edit Model Details" : "Register New Model"}
            </DialogTitle>
            <DialogDescription>
              {editingModel
                ? "Update core specifications, deprecation calendars, or parameter definitions."
                : "Register a model in the master global inventory to enable regional matrix toggle syncs."}
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleFormSubmit} className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5 col-span-2 md:col-span-1">
                <label className="text-xs font-semibold text-gray-700">Model ID (Exact provider string)</label>
                <Input
                  value={modelId}
                  onChange={(e) => setModelId(e.target.value)}
                  placeholder="meta/llama-3.1-70b"
                  required
                  disabled={!!editingModel} // Disable mutating model_id for existing records
                />
              </div>
              <div className="space-y-1.5 col-span-2 md:col-span-1">
                <label className="text-xs font-semibold text-gray-700">Display Name</label>
                <Input
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                  placeholder="Llama 3.1 70B"
                  required
                />
              </div>

              <div className="space-y-1.5 col-span-2 md:col-span-1">
                <label className="text-xs font-semibold text-gray-700">Provider</label>
                <Input
                  value={provider}
                  onChange={(e) => setProvider(e.target.value)}
                  placeholder="meta"
                  required
                />
              </div>
              <div className="space-y-1.5 col-span-2 md:col-span-1">
                <label className="text-xs font-semibold text-gray-700">Model Type</label>
                <select
                  value={type}
                  onChange={(e) => setType(e.target.value)}
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-gray-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <option value="chat">Chat / LLM</option>
                  <option value="embedding">Embeddings</option>
                  <option value="rerank">Reranker</option>
                  <option value="image">Image Gen</option>
                </select>
              </div>

              <div className="space-y-1.5 col-span-2 md:col-span-1">
                <label className="text-xs font-semibold text-gray-700">Context Length (Tokens)</label>
                <Input
                  type="number"
                  value={contextLength}
                  onChange={(e) => setContextLength(e.target.value)}
                  placeholder="128000"
                />
              </div>
              <div className="space-y-1.5 col-span-2 md:col-span-1">
                <label className="text-xs font-semibold text-gray-700">Max Output Tokens</label>
                <Input
                  type="number"
                  value={maxOutputTokens}
                  onChange={(e) => setMaxOutputTokens(e.target.value)}
                  placeholder="4096"
                />
              </div>

              <div className="space-y-1.5 col-span-2">
                <label className="text-xs font-semibold text-gray-700">Description</label>
                <textarea
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="Enter a descriptive overview summarizing this model..."
                  rows={2}
                  className="flex min-h-[80px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-gray-900 ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                />
              </div>

              <div className="space-y-1.5 col-span-2 md:col-span-1">
                <label className="text-xs font-semibold text-gray-700 flex items-center gap-1">
                  Real EOL Date
                  <TooltipProvider>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <HelpCircle className="h-3.5 w-3.5 text-gray-400 cursor-help" />
                      </TooltipTrigger>
                      <TooltipContent>The model's official deprecation date from upstream providers.</TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                </label>
                <Input
                  type="date"
                  value={realEol}
                  onChange={(e) => setRealEol(e.target.value)}
                />
              </div>
              <div className="space-y-1.5 col-span-2 md:col-span-1">
                <label className="text-xs font-semibold text-gray-700 flex items-center gap-1">
                  Override EOL Date
                  <TooltipProvider>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <HelpCircle className="h-3.5 w-3.5 text-gray-400 cursor-help" />
                      </TooltipTrigger>
                      <TooltipContent>Deploy an early deprecation calendar locally before upstream shutdown.</TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                </label>
                <Input
                  type="date"
                  value={overrideEol}
                  onChange={(e) => setOverrideEol(e.target.value)}
                />
              </div>

              <div className="space-y-1.5 col-span-2 border p-3 rounded-md flex items-center justify-between bg-gray-50/50 mt-2">
                <div className="flex flex-col">
                  <span className="text-sm font-semibold text-gray-900">Global Activation Status</span>
                  <span className="text-[11px] text-gray-500">If disabled, the model is completely unavailable across all regions.</span>
                </div>
                <Switch
                  checked={isActiveGlobally}
                  onCheckedChange={setIsActiveGlobally}
                />
              </div>

              <div className="space-y-1.5 col-span-2 border p-3 rounded-md bg-transparent mt-2">
                <span className="text-sm font-semibold text-gray-900 block">Rollout to Regions</span>
                <span className="text-[11px] text-gray-500 block mb-3">
                  Deploy this model immediately to the selected regional LiteLLM instances.
                </span>
                {regions.length === 0 ? (
                  <span className="text-xs text-gray-400 italic block">No active regions available.</span>
                ) : (
                  <div className="grid grid-cols-2 gap-2">
                    {regions.map((region) => {
                      const isChecked = selectedRegionIds.includes(region.id);
                      return (
                        <label
                          key={region.id}
                          className={`flex items-center gap-2 p-2 rounded-md border text-sm transition-all cursor-pointer ${
                            isChecked
                              ? "bg-gray-50/50 border-gray-400 text-gray-900"
                              : "bg-transparent border-gray-100 text-gray-500 hover:bg-gray-50/30"
                          }`}
                        >
                          <input
                            type="checkbox"
                            checked={isChecked}
                            onChange={(e) => {
                              if (e.target.checked) {
                                setSelectedRegionIds((prev) => [...prev, region.id]);
                              } else {
                                setSelectedRegionIds((prev) => prev.filter((id) => id !== region.id));
                              }
                            }}
                            className="rounded border-gray-300 text-gray-900 focus:ring-gray-900 h-4 w-4"
                          />
                          <span className="font-medium">{region.name}</span>
                        </label>
                      );
                    })}
                  </div>
                )}
              </div>

              <div className="space-y-1.5 col-span-2">
                <label className="text-xs font-semibold text-gray-700">LiteLLM Parameters (JSON configuration object)</label>
                <textarea
                  value={litellmParams}
                  onChange={(e) => setLitellmParams(e.target.value)}
                  rows={4}
                  className="flex w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-gray-900 font-mono focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                />
              </div>
            </div>

            <DialogFooter className="pt-4 border-t border-gray-100">
              <Button type="button" variant="ghost" onClick={() => setIsFormOpen(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={saveModelMutation.isPending}>
                {saveModelMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                {editingModel ? "Save Changes" : "Create Model"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* CONFIRM DELETE DIALOG */}
      <Dialog open={isDeleteOpen} onOpenChange={setIsDeleteOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="text-xl font-bold flex items-center gap-2 text-red-600">
              <Trash2 className="h-5 w-5" />
              Confirm Soft-Deletion
            </DialogTitle>
            <DialogDescription>
              This will mark the model as deleted in the master database and trigger background deregistration tasks across all associated regions. Are you sure?
            </DialogDescription>
          </DialogHeader>
          {modelToDelete && (
            <div className="bg-gray-50 border rounded-lg p-3 space-y-1.5">
              <div className="text-sm font-bold text-gray-900">{modelToDelete.display_name}</div>
              <div className="text-xs text-gray-500 font-mono">{modelToDelete.model_id}</div>
              <div className="text-xs text-red-600 pt-1 flex items-center gap-1">
                <Info className="h-3 w-3" />
                This action is reversible but will immediately terminate model access routes.
              </div>
            </div>
          )}
          <DialogFooter className="pt-4 border-t border-gray-100">
            <Button type="button" variant="ghost" onClick={() => setIsDeleteOpen(false)}>
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              onClick={() => modelToDelete && deleteModelMutation.mutate(modelToDelete.id)}
              disabled={deleteModelMutation.isPending}
            >
              {deleteModelMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Soft Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* IMPORT FROM REGION DIALOG */}
      <Dialog open={isImportOpen} onOpenChange={setIsImportOpen}>
        <DialogContent className="max-w-3xl max-h-[85vh] flex flex-col">
          <DialogHeader>
            <DialogTitle className="text-xl font-bold flex items-center gap-2">
              <RefreshCw className="h-5 w-5 text-gray-700" />
              Import Model from Region
            </DialogTitle>
            <DialogDescription>
              Select an active region to inspect models configured on its LiteLLM instance but absent from your database.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4 my-2 flex-1 overflow-y-auto pr-1">
            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-gray-700">Source Region</label>
              <select
                value={importRegionId}
                onChange={(e) => {
                  const val = e.target.value;
                  setImportRegionId(val);
                  fetchImportableModels(val);
                }}
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-gray-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              >
                <option value="">-- Select a Region --</option>
                {regions.map((reg) => (
                  <option key={reg.id} value={String(reg.id)}>
                    {reg.name}
                  </option>
                ))}
              </select>
            </div>

            {isImportableLoading ? (
              <div className="flex flex-col items-center justify-center py-12 space-y-3">
                <Loader2 className="h-8 w-8 animate-spin text-gray-400" />
                <span className="text-sm text-gray-500">Querying regional LiteLLM proxy...</span>
              </div>
            ) : importRegionId && importableModels.length === 0 ? (
              <div className="text-center py-12 border border-dashed rounded-md bg-transparent">
                <Cpu className="h-10 w-10 text-gray-300 mx-auto mb-2" />
                <span className="text-sm text-gray-500 font-medium block">No Importable Models</span>
                <span className="text-xs text-gray-400 block mt-1">
                  All models discovered in this region are already synchronized.
                </span>
              </div>
            ) : !importRegionId ? (
              <div className="text-center py-12 border border-dashed rounded-md bg-transparent">
                <Globe2 className="h-10 w-10 text-gray-300 mx-auto mb-2" />
                <span className="text-sm text-gray-400">Please choose a region to view models available for import.</span>
              </div>
            ) : (
              <div className="border rounded-md overflow-hidden">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Model Spec</TableHead>
                      <TableHead>Provider</TableHead>
                      <TableHead>Type</TableHead>
                      <TableHead className="text-right">Action</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {importableModels.map((model) => (
                      <TableRow key={model.model_id}>
                        <TableCell>
                          <div className="flex flex-col">
                            <span className="text-sm font-bold text-gray-900">{model.display_name}</span>
                            <span className="text-xs text-gray-500 font-mono mt-0.5">{model.model_id}</span>
                            {model.description && (
                              <span className="text-[10px] text-gray-400 line-clamp-1 mt-0.5">{model.description}</span>
                            )}
                          </div>
                        </TableCell>
                        <TableCell className="capitalize text-sm text-gray-700">{model.provider}</TableCell>
                        <TableCell>
                          <Badge variant="outline" className="capitalize text-[10px] bg-transparent text-gray-600 border-gray-200">
                            {model.type}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-right">
                          <Button
                            variant="outline"
                            size="sm"
                            type="button"
                            className="text-xs bg-transparent border-gray-200 hover:bg-gray-50 text-gray-700"
                            onClick={() => {
                              setIsImportOpen(false);
                              
                              // Populate form states
                              setModelId(model.model_id);
                              setDisplayName(model.display_name);
                              setProvider(model.provider);
                              setType(model.type);
                              setContextLength(model.context_length ? String(model.context_length) : "");
                              setMaxOutputTokens(model.max_output_tokens ? String(model.max_output_tokens) : "");
                              setDescription(model.description || "");
                              setRealEol("");
                              setOverrideEol("");
                              setIsActiveGlobally(true);
                              setLitellmParams(model.litellm_params ? JSON.stringify(model.litellm_params, null, 2) : "{}");
                              
                              // Pre-check the source region and route submit to /import
                              setSelectedRegionIds([Number(importRegionId)]);
                              setImportSourceRegionId(Number(importRegionId));

                              // Open main form dialog
                              setIsFormOpen(true);
                            }}
                          >
                            Prep Import
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </div>

          <DialogFooter className="pt-4 border-t border-gray-100">
            <Button type="button" variant="ghost" onClick={() => setIsImportOpen(false)}>
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
