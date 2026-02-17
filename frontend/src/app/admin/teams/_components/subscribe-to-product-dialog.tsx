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
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useTeams } from "@/hooks/use-teams";
import { Product } from "@/types/product";
import { get } from "@/utils/api";
import { useQuery } from "@tanstack/react-query";

interface SubscribeToProductDialogProps {
  teamId: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function SubscribeToProductDialog({
  teamId,
  open,
  onOpenChange,
}: SubscribeToProductDialogProps) {
  const [selectedProductId, setSelectedProductId] = useState("");
  const { subscribeToProduct, isSubscribing } = useTeams();

  const { data: allProducts = [], isLoading: isLoadingAllProducts } = useQuery<
    Product[]
  >({
    queryKey: ["products"],
    queryFn: async () => {
      const response = await get("/products");
      return response.json();
    },
    enabled: open,
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!teamId || !selectedProductId) return;

    subscribeToProduct(
      {
        teamId,
        productId: selectedProductId,
      },
      {
        onSuccess: () => {
          onOpenChange(false);
          setSelectedProductId("");
        },
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Subscribe Team to Product</DialogTitle>
          <DialogDescription>
            Select a product to subscribe this team to.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit}>
          <div className="grid gap-4 py-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">Product</label>
              <Select
                value={selectedProductId}
                onValueChange={setSelectedProductId}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select a product" />
                </SelectTrigger>
                <SelectContent>
                  {isLoadingAllProducts ? (
                    <SelectItem value="" disabled>
                      Loading products...
                    </SelectItem>
                  ) : allProducts.length > 0 ? (
                    allProducts.map((product) => (
                      <SelectItem key={product.id} value={product.id}>
                        {product.name} ({product.id})
                      </SelectItem>
                    ))
                  ) : (
                    <SelectItem value="" disabled>
                      No products available
                    </SelectItem>
                  )}
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={isSubscribing || !selectedProductId}
            >
              {isSubscribing && (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              )}
              Subscribe
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
