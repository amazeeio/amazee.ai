import { useState, useEffect } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useToast } from '@/hooks/use-toast';
import { put } from '@/utils/api';
import { Product } from '@/types/product';
import { Loader2 } from 'lucide-react';

interface EditProductDialogProps {
  product: Product | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function EditProductDialog({ product, open, onOpenChange }: EditProductDialogProps) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [formData, setFormData] = useState<Partial<Product>>({});

  useEffect(() => {
    if (product) {
      setFormData(product);
    }
  }, [product]);

  const updateProductMutation = useMutation({
    mutationFn: async ({ id, data }: { id: string; data: Partial<Product> }) => {
      const response = await put(`/products/${id}`, data);
      return response.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['products'] });
      onOpenChange(false);
      toast({
        title: "Success",
        description: "Product updated successfully"
      });
    },
    onError: (error: Error) => {
      toast({
        variant: "destructive",
        title: "Error",
        description: error.message
      });
    },
  });

  const handleUpdate = () => {
    if (!product) return;
    updateProductMutation.mutate({ id: product.id, data: formData });
  };

  if (!product) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Edit Product</DialogTitle>
        </DialogHeader>
        <div className="grid grid-cols-2 gap-4">
          <div className="col-span-2">
            <Label htmlFor="edit-id">Product ID (Stripe ID)</Label>
            <Input
              id="edit-id"
              value={formData.id || ''}
              readOnly
              className="bg-muted"
            />
          </div>
          <div className="col-span-2">
            <Label htmlFor="edit-name">Name</Label>
            <Input
              id="edit-name"
              value={formData.name || ''}
              onChange={(e) => setFormData({ ...formData, name: e.target.value })}
            />
          </div>
          <div>
            <Label htmlFor="edit-user-count">User Count</Label>
            <Input
              id="edit-user-count"
              type="number"
              value={formData.user_count || 1}
              onChange={(e) => setFormData({ ...formData, user_count: parseInt(e.target.value) })}
            />
          </div>
          <div>
            <Label htmlFor="edit-keys-per-user">Keys per User</Label>
            <Input
              id="edit-keys-per-user"
              type="number"
              value={formData.keys_per_user || 1}
              onChange={(e) => setFormData({ ...formData, keys_per_user: parseInt(e.target.value) })}
            />
          </div>
          <div>
            <Label htmlFor="edit-total-key-count">Total Key Count</Label>
            <Input
              id="edit-total-key-count"
              type="number"
              value={formData.total_key_count || 1}
              onChange={(e) => setFormData({ ...formData, total_key_count: parseInt(e.target.value) })}
            />
          </div>
          <div>
            <Label htmlFor="edit-service-key-count">Service Key Count</Label>
            <Input
              id="edit-service-key-count"
              type="number"
              value={formData.service_key_count || 5}
              onChange={(e) => setFormData({ ...formData, service_key_count: parseInt(e.target.value) })}
            />
          </div>
          <div>
            <Label htmlFor="edit-max-budget-per-key">Max Budget per Key</Label>
            <Input
              id="edit-max-budget-per-key"
              type="number"
              step="0.01"
              min="0"
              required
              value={formData.max_budget_per_key ?? 20.0}
              onChange={(e) => {
                const value = parseFloat(e.target.value);
                setFormData({ ...formData, max_budget_per_key: isNaN(value) ? 20.0 : value });
              }}
            />
          </div>
          <div>
            <Label htmlFor="edit-rpm-per-key">RPM per Key</Label>
            <Input
              id="edit-rpm-per-key"
              type="number"
              value={formData.rpm_per_key || 500}
              onChange={(e) => setFormData({ ...formData, rpm_per_key: parseInt(e.target.value) })}
            />
          </div>
          <div>
            <Label htmlFor="edit-vector-db-count">Vector DB Count</Label>
            <Input
              id="edit-vector-db-count"
              type="number"
              value={formData.vector_db_count || 1}
              onChange={(e) => setFormData({ ...formData, vector_db_count: parseInt(e.target.value) })}
            />
          </div>
          <div>
            <Label htmlFor="edit-vector-db-storage">Vector DB Storage (GiB)</Label>
            <Input
              id="edit-vector-db-storage"
              type="number"
              value={formData.vector_db_storage || 50}
              onChange={(e) => setFormData({ ...formData, vector_db_storage: parseInt(e.target.value) })}
            />
          </div>
          <div>
            <Label htmlFor="edit-renewal-period-days">Renewal Period (Days)</Label>
            <Input
              id="edit-renewal-period-days"
              type="number"
              required
              value={formData.renewal_period_days || 31}
              onChange={(e) => setFormData({ ...formData, renewal_period_days: parseInt(e.target.value) })}
            />
          </div>
          <div className="col-span-2">
            <div className="flex items-center space-x-2">
              <input
                type="checkbox"
                id="edit-active"
                checked={formData.active !== false}
                onChange={(e) => setFormData({ ...formData, active: e.target.checked })}
                className="h-4 w-4 rounded border-gray-300"
              />
              <Label htmlFor="edit-active">Active</Label>
            </div>
          </div>
        </div>
        <div className="mt-4 flex justify-end">
          <Button
            onClick={handleUpdate}
            disabled={updateProductMutation.isPending}
          >
            {updateProductMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            Update
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
