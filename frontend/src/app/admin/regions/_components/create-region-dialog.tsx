import { Loader2 } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { useToast } from "@/hooks/use-toast";
import { post } from "@/utils/api";
import { useMutation, useQueryClient } from "@tanstack/react-query";

interface CreateRegionDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function CreateRegionDialog({
  open,
  onOpenChange,
}: CreateRegionDialogProps) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [newRegion, setNewRegion] = useState({
    name: "",
    label: "",
    description: "",
    postgres_host: "",
    postgres_port: 5432,
    postgres_admin_user: "",
    postgres_admin_password: "",
    litellm_api_url: "",
    litellm_api_key: "",
    is_dedicated: false,
  });

  const createRegionMutation = useMutation({
    mutationFn: async (regionData: typeof newRegion) => {
      const response = await post("regions", regionData);
      return response.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["regions"] });
      onOpenChange(false);
      setNewRegion({
        name: "",
        label: "",
        description: "",
        postgres_host: "",
        postgres_port: 5432,
        postgres_admin_user: "",
        postgres_admin_password: "",
        litellm_api_url: "",
        litellm_api_key: "",
        is_dedicated: false,
      });
      toast({
        title: "Success",
        description: "Region created successfully",
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
    createRegionMutation.mutate(newRegion);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogTrigger asChild>
        <Button>Add Region</Button>
      </DialogTrigger>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Add New Region</DialogTitle>
          <DialogDescription>
            Create a new region for hosting private AI databases.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">Name</label>
              <Input
                value={newRegion.name}
                onChange={(e) =>
                  setNewRegion({ ...newRegion, name: e.target.value })
                }
                placeholder="us-east-1"
                required
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">Label</label>
              <Input
                value={newRegion.label}
                onChange={(e) =>
                  setNewRegion({ ...newRegion, label: e.target.value })
                }
                placeholder="US East 1"
                required
              />
            </div>
            <div className="space-y-2 col-span-2">
              <label className="text-sm font-medium">Description</label>
              <textarea
                value={newRegion.description}
                onChange={(e) =>
                  setNewRegion({ ...newRegion, description: e.target.value })
                }
                placeholder="Optional description for this region"
                className="flex min-h-[80px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                rows={3}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">Postgres Host</label>
              <Input
                value={newRegion.postgres_host}
                onChange={(e) =>
                  setNewRegion({ ...newRegion, postgres_host: e.target.value })
                }
                placeholder="db.example.com"
                required
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">Postgres Port</label>
              <Input
                type="number"
                value={newRegion.postgres_port}
                onChange={(e) =>
                  setNewRegion({
                    ...newRegion,
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
                value={newRegion.postgres_admin_user}
                onChange={(e) =>
                  setNewRegion({
                    ...newRegion,
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
                value={newRegion.postgres_admin_password}
                onChange={(e) =>
                  setNewRegion({
                    ...newRegion,
                    postgres_admin_password: e.target.value,
                  })
                }
                placeholder="••••••••"
                required
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">LiteLLM API URL</label>
              <Input
                value={newRegion.litellm_api_url}
                onChange={(e) =>
                  setNewRegion({
                    ...newRegion,
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
                value={newRegion.litellm_api_key}
                onChange={(e) =>
                  setNewRegion({
                    ...newRegion,
                    litellm_api_key: e.target.value,
                  })
                }
                placeholder="••••••••"
                required
              />
            </div>
          </div>
          <div className="flex items-center space-x-2">
            <input
              type="checkbox"
              id="is_dedicated"
              checked={newRegion.is_dedicated}
              onChange={(e) =>
                setNewRegion({ ...newRegion, is_dedicated: e.target.checked })
              }
              className="h-4 w-4 rounded border-gray-300"
            />
            <label htmlFor="is_dedicated" className="text-sm font-medium">
              Dedicated Region (can be assigned to specific teams)
            </label>
          </div>
          <DialogFooter>
            <Button type="submit" disabled={createRegionMutation.isPending}>
              {createRegionMutation.isPending ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Creating...
                </>
              ) : (
                "Create Region"
              )}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
