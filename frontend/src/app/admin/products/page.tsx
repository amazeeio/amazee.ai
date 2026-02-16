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
  TablePagination,
  useTablePagination,
} from "@/components/ui/table";
import { useToast } from '@/hooks/use-toast';
import { TableActionButtons } from '@/components/ui/table-action-buttons';
import { LimitsView } from '@/components/ui/limits-view';
import { get, del } from '@/utils/api';
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Loader2 } from 'lucide-react';
import React from 'react';

import { Product } from '@/types/product';
import { PricingTables } from '@/types/pricing-table';
import { CreateProductDialog } from './_components/create-product-dialog';
import { EditProductDialog } from './_components/edit-product-dialog';
import { PricingTableDialog } from './_components/pricing-table-dialog';
import { PricingTablesList } from './_components/pricing-tables-list';

export default function ProductsPage() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false);
  const [isEditDialogOpen, setIsEditDialogOpen] = useState(false);
  const [isPricingTableDialogOpen, setIsPricingTableDialogOpen] = useState(false);
  const [selectedProduct, setSelectedProduct] = useState<Product | null>(null);

  // Queries
  const { data: products = [], isLoading: isLoadingProducts } = useQuery<Product[]>({
    queryKey: ['products'],
    queryFn: async () => {
      const response = await get('/products');
      return response.json();
    },
  });

  // Pricing Table Query
  const { data: pricingTables, isLoading: isLoadingPricingTables } = useQuery<PricingTables>({
    queryKey: ['pricing-tables'],
    queryFn: async () => {
      const response = await get('/pricing-tables/list');
      return response.json();
    },
  });

  // System Limits Query
  const { data: systemLimits = [], isLoading: isLoadingSystemLimits } = useQuery({
    queryKey: ['system-limits'],
    queryFn: async () => {
      const response = await get('/limits/system');
      return response.json();
    },
  });

  // Mutations
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

  const handleDelete = (id: string) => {
    deleteProductMutation.mutate(id);
  };

  // Pagination
  const {
    currentPage,
    pageSize,
    totalPages,
    totalItems,
    paginatedData,
    goToPage,
    changePageSize,
  } = useTablePagination(products, 10);

  if (isLoadingProducts || isLoadingPricingTables || isLoadingSystemLimits) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <Loader2 className="h-8 w-8 animate-spin" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h1 className="text-3xl font-bold">Product Management</h1>
        <CreateProductDialog 
          open={isCreateDialogOpen} 
          onOpenChange={setIsCreateDialogOpen} 
        />
      </div>

      <Tabs defaultValue="products" className="space-y-4">
        <TabsList>
          <TabsTrigger value="products">Products</TabsTrigger>
          <TabsTrigger value="pricing-tables">Pricing Tables</TabsTrigger>
          <TabsTrigger value="system-limits">System Limits</TabsTrigger>
        </TabsList>

        <TabsContent value="products" className="space-y-4">
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
              {paginatedData.map((product) => (
                <TableRow key={product.id}>
                  <TableCell className="font-mono text-sm">{product.id}</TableCell>
                  <TableCell>{product.name}</TableCell>
                  <TableCell>{product.user_count}</TableCell>
                  <TableCell>{product.keys_per_user}</TableCell>
                  <TableCell>{product.total_key_count}</TableCell>
                  <TableCell>{product.service_key_count}</TableCell>
                  <TableCell>${product.max_budget_per_key ? product.max_budget_per_key.toFixed(2) : '0.00'}</TableCell>
                  <TableCell>{product.rpm_per_key}</TableCell>
                  <TableCell>{product.vector_db_count}</TableCell>
                  <TableCell>{product.vector_db_storage}</TableCell>
                  <TableCell>{product.renewal_period_days}</TableCell>
                  <TableCell>
                    <span className={`inline-flex items-center px-2 py-1 rounded-full text-xs font-medium ${product.active ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'
                      }`}>
                      {product.active ? 'Active' : 'Inactive'}
                    </span>
                  </TableCell>
                  <TableCell>{new Date(product.created_at).toLocaleDateString()}</TableCell>
                  <TableCell>
                    <TableActionButtons
                      onEdit={() => {
                        setSelectedProduct(product);
                        setIsEditDialogOpen(true);
                      }}
                      onDelete={() => handleDelete(product.id)}
                      deleteTitle="Delete Product"
                      deleteDescription="Are you sure you want to delete this product? This action cannot be undone."
                      isDeleting={deleteProductMutation.isPending}
                    />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>

          <TablePagination
            currentPage={currentPage}
            totalPages={totalPages}
            pageSize={pageSize}
            totalItems={totalItems}
            onPageChange={goToPage}
            onPageSizeChange={changePageSize}
          />
        </TabsContent>

        <TabsContent value="pricing-tables" className="space-y-4">
          <div className="flex justify-between items-center">
            <h2 className="text-xl font-semibold">Pricing Tables</h2>
            <PricingTableDialog 
              open={isPricingTableDialogOpen} 
              onOpenChange={setIsPricingTableDialogOpen} 
            />
          </div>
          <PricingTablesList pricingTables={pricingTables} />
        </TabsContent>

        <TabsContent value="system-limits" className="space-y-4">
          <div className="flex justify-between items-center">
            <div>
              <h2 className="text-xl font-semibold">System Default Limits</h2>
              <p className="text-sm text-muted-foreground">
                These are the default limits applied to all teams and users when no product-specific or manual limits are set.
              </p>
            </div>
          </div>

          <LimitsView
            limits={systemLimits}
            isLoading={isLoadingSystemLimits}
            ownerType="system"
            ownerId="0"
            queryKey={['system-limits']}
            showResetAll={false}
            allowIndividualReset={false}
          />
        </TabsContent>
      </Tabs>

      <EditProductDialog 
        product={selectedProduct} 
        open={isEditDialogOpen} 
        onOpenChange={setIsEditDialogOpen} 
      />
    </div>
  );
}
