'use client';

import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
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
import { useToast } from '@/hooks/use-toast';
import { get, post, put, del } from '@/utils/api';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

interface Product {
  id: string;
  name: string;
  user_count: number;
  keys_per_user: number;
  total_key_count: number;
  service_key_count: number;
  max_budget_per_key: number;
  rpm_per_key: number;
  vector_db_count: number;
  vector_db_storage: number;
  renewal_period_days: number;
  active: boolean;
  created_at: string;
}

interface PricingTable {
  pricing_table_id: string;
  updated_at: string;
}

interface PricingTables {
  standard: PricingTable | null;
  always_free: PricingTable | null;
}

export default function ProductsPage() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false);
  const [isEditDialogOpen, setIsEditDialogOpen] = useState(false);
  const [isPricingTableDialogOpen, setIsPricingTableDialogOpen] = useState(false);
  const [selectedProduct, setSelectedProduct] = useState<Product | null>(null);
  const [formData, setFormData] = useState<Partial<Product>>({});
  const [pricingTableId, setPricingTableId] = useState('');
  const [pricingTableType, setPricingTableType] = useState<'standard' | 'always_free'>('standard');

  // Update form data
  const updateFormData = (newData: Partial<Product>) => {
    setFormData(newData);
  };

  // Queries
  const { data: products = [] } = useQuery<Product[]>({
    queryKey: ['products'],
    queryFn: async () => {
      const response = await get('/products');
      return response.json();
    },
  });

  // Pricing Table Query
  const { data: pricingTables } = useQuery<PricingTables>({
    queryKey: ['pricing-tables'],
    queryFn: async () => {
      const response = await get('/pricing-tables/list');
      return response.json();
    },
  });

  // Mutations
  const createProductMutation = useMutation({
    mutationFn: async (productData: Partial<Product>) => {
      const response = await post('/products', productData);
      return response.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['products'] });
      setIsCreateDialogOpen(false);
      setFormData({});
      toast({
        title: "Success",
        description: "Product created successfully"
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

  const updateProductMutation = useMutation({
    mutationFn: async ({ id, data }: { id: string; data: Partial<Product> }) => {
      const response = await put(`/products/${id}`, data);
      return response.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['products'] });
      setIsEditDialogOpen(false);
      setSelectedProduct(null);
      setFormData({});
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

  const deleteProductMutation = useMutation({
    mutationFn: async (id: string) => {
      await del(`/products/${id}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['products'] });
      toast({
        title: "Success",
        description: "Product deleted successfully"
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

  // Pricing Table Mutations
  const updatePricingTableMutation = useMutation({
    mutationFn: async (data: { pricing_table_id: string; table_type: 'standard' | 'always_free' }) => {
      const response = await post('/pricing-tables', data);
      return response.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['pricing-tables'] });
      setIsPricingTableDialogOpen(false);
      setPricingTableId('');
      setPricingTableType('standard');
      toast({
        title: "Success",
        description: "Pricing table updated successfully"
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

  const deletePricingTableMutation = useMutation({
    mutationFn: async () => {
      await del('/pricing-tables');
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['pricing-tables'] });
      toast({
        title: "Success",
        description: "Pricing table deleted successfully"
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

  const handleCreate = () => {
    createProductMutation.mutate(formData);
  };

  const handleUpdate = () => {
    if (!selectedProduct) return;
    updateProductMutation.mutate({ id: selectedProduct.id, data: formData });
  };

  const handleDelete = (id: string) => {
    if (!confirm('Are you sure you want to delete this product?')) return;
    deleteProductMutation.mutate(id);
  };

  const handleUpdatePricingTable = () => {
    updatePricingTableMutation.mutate({
      pricing_table_id: pricingTableId,
      table_type: pricingTableType
    });
  };

  const handleDeletePricingTable = () => {
    if (!confirm('Are you sure you want to delete the current pricing table?')) return;
    deletePricingTableMutation.mutate();
  };

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h1 className="text-3xl font-bold">Product Management</h1>
        <div className="space-x-4">
          <Dialog open={isPricingTableDialogOpen} onOpenChange={setIsPricingTableDialogOpen}>
            <DialogTrigger asChild>
              <Button variant="outline">Manage Pricing Table</Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Manage Pricing Table</DialogTitle>
              </DialogHeader>
              <div className="space-y-4">
                <div>
                  <Label htmlFor="pricing-table-type">Table Type</Label>
                  <Select
                    value={pricingTableType}
                    onValueChange={(value: 'standard' | 'always_free') => setPricingTableType(value)}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="Select table type" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="standard">Standard</SelectItem>
                      <SelectItem value="always_free">Always Free</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div>
                  <Label htmlFor="pricing-table-id">Stripe Pricing Table ID</Label>
                  <Input
                    id="pricing-table-id"
                    placeholder="prctbl_XXX"
                    value={pricingTableId}
                    onChange={(e) => setPricingTableId(e.target.value)}
                  />
                </div>
                <div className="space-y-2">
                  <div className="text-sm font-medium">Current Pricing Tables</div>
                  <div className="grid gap-2">
                    <div className="text-sm">
                      <span className="font-medium">Standard:</span>{' '}
                      {pricingTables?.standard ? (
                        <span className="text-muted-foreground">
                          {pricingTables.standard.pricing_table_id}
                          <br />
                          <span className="text-xs">
                            Last updated: {new Date(pricingTables.standard.updated_at).toLocaleString()}
                          </span>
                        </span>
                      ) : (
                        <span className="text-muted-foreground">Not set</span>
                      )}
                    </div>
                    <div className="text-sm">
                      <span className="font-medium">Always Free:</span>{' '}
                      {pricingTables?.always_free ? (
                        <span className="text-muted-foreground">
                          {pricingTables.always_free.pricing_table_id}
                          <br />
                          <span className="text-xs">
                            Last updated: {new Date(pricingTables.always_free.updated_at).toLocaleString()}
                          </span>
                        </span>
                      ) : (
                        <span className="text-muted-foreground">Not set</span>
                      )}
                    </div>
                  </div>
                </div>
                <div className="flex justify-between">
                  <Button
                    variant="destructive"
                    onClick={handleDeletePricingTable}
                    disabled={!pricingTables?.standard && !pricingTables?.always_free}
                  >
                    Delete Current Table
                  </Button>
                  <Button onClick={handleUpdatePricingTable}>
                    Update Pricing Table
                  </Button>
                </div>
              </div>
            </DialogContent>
          </Dialog>
          <Dialog open={isCreateDialogOpen} onOpenChange={setIsCreateDialogOpen}>
            <DialogTrigger asChild>
              <Button>Create Product</Button>
            </DialogTrigger>
            <DialogContent className="max-w-2xl">
              <DialogHeader>
                <DialogTitle>Create New Product</DialogTitle>
              </DialogHeader>
              <div className="grid grid-cols-2 gap-4">
                <div className="col-span-2">
                  <Label htmlFor="id">Product ID (Stripe ID)</Label>
                  <Input
                    id="id"
                    placeholder="prod_XXX"
                    value={formData.id || ''}
                    onChange={(e) => updateFormData({ ...formData, id: e.target.value })}
                  />
                </div>
                <div className="col-span-2">
                  <Label htmlFor="name">Name</Label>
                  <Input
                    id="name"
                    value={formData.name || ''}
                    onChange={(e) => updateFormData({ ...formData, name: e.target.value })}
                  />
                </div>
                <div>
                  <Label htmlFor="user_count">User Count</Label>
                  <Input
                    id="user_count"
                    type="number"
                    value={formData.user_count || 1}
                    onChange={(e) => updateFormData({ ...formData, user_count: parseInt(e.target.value) })}
                  />
                </div>
                <div>
                  <Label htmlFor="keys_per_user">Keys per User</Label>
                  <Input
                    id="keys_per_user"
                    type="number"
                    value={formData.keys_per_user || 1}
                    onChange={(e) => updateFormData({ ...formData, keys_per_user: parseInt(e.target.value) })}
                  />
                </div>
                <div>
                  <Label htmlFor="total_key_count">Total Key Count</Label>
                  <Input
                    id="total_key_count"
                    type="number"
                    value={formData.total_key_count || 1}
                    onChange={(e) => updateFormData({ ...formData, total_key_count: parseInt(e.target.value) })}
                  />
                </div>
                <div>
                  <Label htmlFor="service_key_count">Service Key Count</Label>
                  <Input
                    id="service_key_count"
                    type="number"
                    value={formData.service_key_count || 5}
                    onChange={(e) => updateFormData({ ...formData, service_key_count: parseInt(e.target.value) })}
                  />
                </div>
                <div>
                  <Label htmlFor="max_budget_per_key">Max Budget per Key</Label>
                  <Input
                    id="max_budget_per_key"
                    type="number"
                    step="0.01"
                    value={formData.max_budget_per_key || 20.0}
                    onChange={(e) => updateFormData({ ...formData, max_budget_per_key: parseFloat(e.target.value) })}
                  />
                </div>
                <div>
                  <Label htmlFor="rpm_per_key">RPM per Key</Label>
                  <Input
                    id="rpm_per_key"
                    type="number"
                    value={formData.rpm_per_key || 500}
                    onChange={(e) => updateFormData({ ...formData, rpm_per_key: parseInt(e.target.value) })}
                  />
                </div>
                <div>
                  <Label htmlFor="vector_db_count">Vector DB Count</Label>
                  <Input
                    id="vector_db_count"
                    type="number"
                    value={formData.vector_db_count || 1}
                    onChange={(e) => updateFormData({ ...formData, vector_db_count: parseInt(e.target.value) })}
                  />
                </div>
                <div>
                  <Label htmlFor="vector_db_storage">Vector DB Storage (GiB)</Label>
                  <Input
                    id="vector_db_storage"
                    type="number"
                    value={formData.vector_db_storage || 50}
                    onChange={(e) => updateFormData({ ...formData, vector_db_storage: parseInt(e.target.value) })}
                  />
                </div>
                <div>
                  <Label htmlFor="renewal_period_days">Renewal Period (Days)</Label>
                  <Input
                    id="renewal_period_days"
                    type="number"
                    required
                    value={formData.renewal_period_days || 30}
                    onChange={(e) => updateFormData({ ...formData, renewal_period_days: parseInt(e.target.value) })}
                  />
                </div>
                <div className="col-span-2">
                  <div className="flex items-center space-x-2">
                    <input
                      type="checkbox"
                      id="active"
                      checked={formData.active !== false}
                      onChange={(e) => updateFormData({ ...formData, active: e.target.checked })}
                      className="h-4 w-4 rounded border-gray-300"
                    />
                    <Label htmlFor="active">Active</Label>
                  </div>
                </div>
              </div>
              <div className="mt-4 flex justify-end">
                <Button onClick={handleCreate}>Create</Button>
              </div>
            </DialogContent>
          </Dialog>
        </div>
      </div>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>ID</TableHead>
            <TableHead>Name</TableHead>
            <TableHead>User Count</TableHead>
            <TableHead>Keys/User</TableHead>
            <TableHead>Total Keys</TableHead>
            <TableHead>Service Keys</TableHead>
            <TableHead>Budget/Key</TableHead>
            <TableHead>RPM/Key</TableHead>
            <TableHead>Vector DBs</TableHead>
            <TableHead>Storage (GiB)</TableHead>
            <TableHead>Renewal (Days)</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Created</TableHead>
            <TableHead>Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {products.map((product) => (
            <TableRow key={product.id}>
              <TableCell className="font-mono text-sm">{product.id}</TableCell>
              <TableCell>{product.name}</TableCell>
              <TableCell>{product.user_count}</TableCell>
              <TableCell>{product.keys_per_user}</TableCell>
              <TableCell>{product.total_key_count}</TableCell>
              <TableCell>{product.service_key_count}</TableCell>
              <TableCell>${product.max_budget_per_key.toFixed(2)}</TableCell>
              <TableCell>{product.rpm_per_key}</TableCell>
              <TableCell>{product.vector_db_count}</TableCell>
              <TableCell>{product.vector_db_storage}</TableCell>
              <TableCell>{product.renewal_period_days}</TableCell>
              <TableCell>
                <span className={`inline-flex items-center px-2 py-1 rounded-full text-xs font-medium ${
                  product.active ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'
                }`}>
                  {product.active ? 'Active' : 'Inactive'}
                </span>
              </TableCell>
              <TableCell>{new Date(product.created_at).toLocaleDateString()}</TableCell>
              <TableCell>
                <div className="space-x-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      setSelectedProduct(product);
                      setFormData(product);
                      setIsEditDialogOpen(true);
                    }}
                  >
                    Edit
                  </Button>
                  <Button
                    variant="destructive"
                    size="sm"
                    onClick={() => handleDelete(product.id)}
                  >
                    Delete
                  </Button>
                </div>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>

      <Dialog open={isEditDialogOpen} onOpenChange={setIsEditDialogOpen}>
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
                onChange={(e) => updateFormData({ ...formData, name: e.target.value })}
              />
            </div>
            <div>
              <Label htmlFor="edit-user-count">User Count</Label>
              <Input
                id="edit-user-count"
                type="number"
                value={formData.user_count || 1}
                onChange={(e) => updateFormData({ ...formData, user_count: parseInt(e.target.value) })}
              />
            </div>
            <div>
              <Label htmlFor="edit-keys-per-user">Keys per User</Label>
              <Input
                id="edit-keys-per-user"
                type="number"
                value={formData.keys_per_user || 1}
                onChange={(e) => updateFormData({ ...formData, keys_per_user: parseInt(e.target.value) })}
              />
            </div>
            <div>
              <Label htmlFor="edit-total-key-count">Total Key Count</Label>
              <Input
                id="edit-total-key-count"
                type="number"
                value={formData.total_key_count || 1}
                onChange={(e) => updateFormData({ ...formData, total_key_count: parseInt(e.target.value) })}
              />
            </div>
            <div>
              <Label htmlFor="edit-service-key-count">Service Key Count</Label>
              <Input
                id="edit-service-key-count"
                type="number"
                value={formData.service_key_count || 5}
                onChange={(e) => updateFormData({ ...formData, service_key_count: parseInt(e.target.value) })}
              />
            </div>
            <div>
              <Label htmlFor="edit-max-budget-per-key">Max Budget per Key</Label>
              <Input
                id="edit-max-budget-per-key"
                type="number"
                step="0.01"
                value={formData.max_budget_per_key || 20.0}
                onChange={(e) => updateFormData({ ...formData, max_budget_per_key: parseFloat(e.target.value) })}
              />
            </div>
            <div>
              <Label htmlFor="edit-rpm-per-key">RPM per Key</Label>
              <Input
                id="edit-rpm-per-key"
                type="number"
                value={formData.rpm_per_key || 500}
                onChange={(e) => updateFormData({ ...formData, rpm_per_key: parseInt(e.target.value) })}
              />
            </div>
            <div>
              <Label htmlFor="edit-vector-db-count">Vector DB Count</Label>
              <Input
                id="edit-vector-db-count"
                type="number"
                value={formData.vector_db_count || 1}
                onChange={(e) => updateFormData({ ...formData, vector_db_count: parseInt(e.target.value) })}
              />
            </div>
            <div>
              <Label htmlFor="edit-vector-db-storage">Vector DB Storage (GiB)</Label>
              <Input
                id="edit-vector-db-storage"
                type="number"
                value={formData.vector_db_storage || 50}
                onChange={(e) => updateFormData({ ...formData, vector_db_storage: parseInt(e.target.value) })}
              />
            </div>
            <div>
              <Label htmlFor="edit-renewal-period-days">Renewal Period (Days)</Label>
              <Input
                id="edit-renewal-period-days"
                type="number"
                required
                value={formData.renewal_period_days || 30}
                onChange={(e) => updateFormData({ ...formData, renewal_period_days: parseInt(e.target.value) })}
              />
            </div>
            <div className="col-span-2">
              <div className="flex items-center space-x-2">
                <input
                  type="checkbox"
                  id="edit-active"
                  checked={formData.active !== false}
                  onChange={(e) => updateFormData({ ...formData, active: e.target.checked })}
                  className="h-4 w-4 rounded border-gray-300"
                />
                <Label htmlFor="edit-active">Active</Label>
              </div>
            </div>
          </div>
          <div className="mt-4 flex justify-end">
            <Button onClick={handleUpdate}>Update</Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}