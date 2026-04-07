import { Loader2 } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useToast } from "@/hooks/use-toast";
import { Product } from "@/types/product";
import { post } from "@/utils/api";
import { useMutation, useQueryClient } from "@tanstack/react-query";

interface CreateProductDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function CreateProductDialog({
  open,
  onOpenChange,
}: CreateProductDialogProps) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [formData, setFormData] = useState<Partial<Product>>({
    user_count: 1,
    keys_per_user: 1,
    total_key_count: 1,
    service_key_count: 5,
    max_budget_per_key: 20.0,
    rpm_per_key: 500,
    vector_db_count: 1,
    vector_db_storage: 50,
    renewal_period_days: 31,
    active: true,
  });

  const createProductMutation = useMutation({
    mutationFn: async (productData: Partial<Product>) => {
      const response = await post("/products", productData);
      return response.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["products"] });
      onOpenChange(false);
      setFormData({
        user_count: 1,
        keys_per_user: 1,
        total_key_count: 1,
        service_key_count: 5,
        max_budget_per_key: 20.0,
        rpm_per_key: 500,
        vector_db_count: 1,
        vector_db_storage: 50,
        renewal_period_days: 31,
        active: true,
      });
      toast({
        title: "Success",
        description: "Product created successfully",
      });
    },
    onError: (error: Error) => {
      toast({
        variant: "destructive",
        title: "Error",
        description: error.message,
      });
    },
  });

  const handleCreate = () => {
    if (!formData.id?.trim() || !formData.name?.trim()) {
      toast({
        variant: "destructive",
        title: "Validation Error",
        description: "Product ID and Name are required fields",
      });
      return;
    }

    createProductMutation.mutate(formData);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogTrigger asChild>
        <Button>Create Product</Button>
      </DialogTrigger>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Create New Product</DialogTitle>
        </DialogHeader>
        <div className="grid grid-cols-2 gap-4">
          <div className="col-span-2">
            <Label htmlFor="id">Product ID (Stripe ID) *</Label>
            <Input
              id="id"
              placeholder="prod_XXX"
              value={formData.id || ""}
              onChange={(e) => setFormData({ ...formData, id: e.target.value })}
              required
            />
          </div>
          <div className="col-span-2">
            <Label htmlFor="name">Name *</Label>
            <Input
              id="name"
              placeholder="Enter product name"
              value={formData.name || ""}
              onChange={(e) =>
                setFormData({ ...formData, name: e.target.value })
              }
              required
            />
          </div>
          <div>
            <Label htmlFor="user_count">User Count</Label>
            <Input
              id="user_count"
              type="number"
              value={formData.user_count || 1}
              onChange={(e) =>
                setFormData({
                  ...formData,
                  user_count: parseInt(e.target.value),
                })
              }
            />
          </div>
          <div>
            <Label htmlFor="keys_per_user">Keys per User</Label>
            <Input
              id="keys_per_user"
              type="number"
              value={formData.keys_per_user || 1}
              onChange={(e) =>
                setFormData({
                  ...formData,
                  keys_per_user: parseInt(e.target.value),
                })
              }
            />
          </div>
          <div>
            <Label htmlFor="total_key_count">Total Key Count</Label>
            <Input
              id="total_key_count"
              type="number"
              value={formData.total_key_count || 1}
              onChange={(e) =>
                setFormData({
                  ...formData,
                  total_key_count: parseInt(e.target.value),
                })
              }
            />
          </div>
          <div>
            <Label htmlFor="service_key_count">Service Key Count</Label>
            <Input
              id="service_key_count"
              type="number"
              value={formData.service_key_count || 5}
              onChange={(e) =>
                setFormData({
                  ...formData,
                  service_key_count: parseInt(e.target.value),
                })
              }
            />
          </div>
          <div>
            <Label htmlFor="max_budget_per_key">Max Budget per Key</Label>
            <Input
              id="max_budget_per_key"
              type="number"
              step="0.01"
              min="0"
              required
              value={formData.max_budget_per_key ?? 20.0}
              onChange={(e) => {
                const value = parseFloat(e.target.value);
                setFormData({
                  ...formData,
                  max_budget_per_key: isNaN(value) ? 20.0 : value,
                });
              }}
            />
          </div>
          <div>
            <Label htmlFor="rpm_per_key">RPM per Key</Label>
            <Input
              id="rpm_per_key"
              type="number"
              value={formData.rpm_per_key || 500}
              onChange={(e) =>
                setFormData({
                  ...formData,
                  rpm_per_key: parseInt(e.target.value),
                })
              }
            />
          </div>
          <div>
            <Label htmlFor="vector_db_count">Vector DB Count</Label>
            <Input
              id="vector_db_count"
              type="number"
              value={formData.vector_db_count || 1}
              onChange={(e) =>
                setFormData({
                  ...formData,
                  vector_db_count: parseInt(e.target.value),
                })
              }
            />
          </div>
          <div>
            <Label htmlFor="vector_db_storage">Vector DB Storage (GiB)</Label>
            <Input
              id="vector_db_storage"
              type="number"
              value={formData.vector_db_storage || 50}
              onChange={(e) =>
                setFormData({
                  ...formData,
                  vector_db_storage: parseInt(e.target.value),
                })
              }
            />
          </div>
          <div>
            <Label htmlFor="renewal_period_days">Renewal Period (Days)</Label>
            <Input
              id="renewal_period_days"
              type="number"
              required
              value={formData.renewal_period_days || 31}
              onChange={(e) =>
                setFormData({
                  ...formData,
                  renewal_period_days: parseInt(e.target.value),
                })
              }
            />
          </div>
          <div className="col-span-2">
            <div className="flex items-center space-x-2">
              <input
                type="checkbox"
                id="active"
                checked={formData.active !== false}
                onChange={(e) =>
                  setFormData({ ...formData, active: e.target.checked })
                }
                className="h-4 w-4 rounded border-gray-300"
              />
              <Label htmlFor="active">Active</Label>
            </div>
          </div>
        </div>
        <div className="mt-4 flex justify-end">
          <Button
            onClick={handleCreate}
            disabled={
              createProductMutation.isPending ||
              !formData.id?.trim() ||
              !formData.name?.trim()
            }
          >
            {createProductMutation.isPending && (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            )}
            Create
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
