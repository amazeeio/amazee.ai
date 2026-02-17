import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { useToast } from "@/hooks/use-toast";
import { Region } from "@/types/region";
import { put } from "@/utils/api";
import { useMutation, useQueryClient } from "@tanstack/react-query";

interface EditRegionDialogProps {
  region: Region | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function EditRegionDialog({
  region,
  open,
  onOpenChange,
}: EditRegionDialogProps) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [editingRegion, setEditingRegion] = useState<Region | null>(null);

  useEffect(() => {
    if (region) {
      setEditingRegion({ ...region });
    }
  }, [region]);

  const updateRegionMutation = useMutation({
    mutationFn: async (regionData: Region) => {
      type UpdateData = {
        name: string;
        label: string;
        description: string;
        postgres_host: string;
        postgres_port: number;
        postgres_admin_user: string;
        litellm_api_url: string;
        is_active: boolean;
        is_dedicated: boolean;
        postgres_admin_password?: string;
        litellm_api_key?: string;
      };

      const updateData: UpdateData = {
        name: regionData.name,
        label: regionData.label,
        description: regionData.description,
        postgres_host: regionData.postgres_host,
        postgres_port: regionData.postgres_port,
        postgres_admin_user: regionData.postgres_admin_user,
        litellm_api_url: regionData.litellm_api_url,
        is_active: regionData.is_active,
        is_dedicated: regionData.is_dedicated,
      };

      // Only include passwords if they are not empty
      if (regionData.postgres_admin_password) {
        updateData.postgres_admin_password = regionData.postgres_admin_password;
      }
      if (regionData.litellm_api_key) {
        updateData.litellm_api_key = regionData.litellm_api_key;
      }

      const response = await put(`regions/${regionData.id}`, updateData);
      return response.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["regions"] });
      onOpenChange(false);
      toast({
        title: "Success",
        description: "Region updated successfully",
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

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingRegion) return;
    updateRegionMutation.mutate(editingRegion);
  };

  if (!editingRegion) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Edit Region</DialogTitle>
          <DialogDescription>
            Update the region configuration.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">Name</label>
              <Input
                value={editingRegion.name}
                onChange={(e) =>
                  setEditingRegion({ ...editingRegion, name: e.target.value })
                }
                placeholder="us-east-1"
                required
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">Label</label>
              <Input
                value={editingRegion.label}
                onChange={(e) =>
                  setEditingRegion({ ...editingRegion, label: e.target.value })
                }
                placeholder="US East 1"
                required
              />
            </div>
            <div className="space-y-2 col-span-2">
              <label className="text-sm font-medium">Description</label>
              <textarea
                value={editingRegion.description}
                onChange={(e) =>
                  setEditingRegion({
                    ...editingRegion,
                    description: e.target.value,
                  })
                }
                placeholder="Optional description for this region"
                className="flex min-h-[80px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                rows={3}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">Postgres Host</label>
              <Input
                value={editingRegion.postgres_host}
                onChange={(e) =>
                  setEditingRegion({
                    ...editingRegion,
                    postgres_host: e.target.value,
                  })
                }
                placeholder="db.example.com"
                required
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">Postgres Port</label>
              <Input
                type="number"
                value={editingRegion.postgres_port}
                onChange={(e) =>
                  setEditingRegion({
                    ...editingRegion,
                    postgres_port: parseInt(e.target.value),
                  })
                }
                placeholder="5432"
                required
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">Admin Username</label>
              <Input
                value={editingRegion.postgres_admin_user}
                onChange={(e) =>
                  setEditingRegion({
                    ...editingRegion,
                    postgres_admin_user: e.target.value,
                  })
                }
                placeholder="admin"
                required
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">Admin Password</label>
              <Input
                type="password"
                value={editingRegion.postgres_admin_password || ""}
                onChange={(e) =>
                  setEditingRegion({
                    ...editingRegion,
                    postgres_admin_password: e.target.value,
                  })
                }
                placeholder="••••••••"
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">LiteLLM API URL</label>
              <Input
                value={editingRegion.litellm_api_url}
                onChange={(e) =>
                  setEditingRegion({
                    ...editingRegion,
                    litellm_api_url: e.target.value,
                  })
                }
                placeholder="https://api.litellm.ai"
                required
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">LiteLLM API Key</label>
              <Input
                type="password"
                value={editingRegion.litellm_api_key || ""}
                onChange={(e) =>
                  setEditingRegion({
                    ...editingRegion,
                    litellm_api_key: e.target.value,
                  })
                }
                placeholder="••••••••"
              />
            </div>
          </div>
          <div className="flex items-center space-x-2">
            <input
              type="checkbox"
              id="edit_is_dedicated"
              checked={editingRegion.is_dedicated}
              onChange={(e) =>
                setEditingRegion({
                  ...editingRegion,
                  is_dedicated: e.target.checked,
                })
              }
              className="h-4 w-4 rounded border-gray-300"
            />
            <label htmlFor="edit_is_dedicated" className="text-sm font-medium">
              Dedicated Region (can be assigned to specific teams)
            </label>
          </div>
          <DialogFooter>
            <Button type="submit" disabled={updateRegionMutation.isPending}>
              {updateRegionMutation.isPending ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Updating...
                </>
              ) : (
                "Update Region"
              )}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
