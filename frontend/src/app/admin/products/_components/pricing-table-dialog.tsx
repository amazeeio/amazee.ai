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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useToast } from "@/hooks/use-toast";
import { post } from "@/utils/api";
import { useMutation, useQueryClient } from "@tanstack/react-query";

interface PricingTableDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function PricingTableDialog({
  open,
  onOpenChange,
}: PricingTableDialogProps) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [pricingTableId, setPricingTableId] = useState("");
  const [pricingTableType, setPricingTableType] = useState<
    "standard" | "always_free" | "gpt"
  >("standard");

  const updatePricingTableMutation = useMutation({
    mutationFn: async (data: {
      pricing_table_id: string;
      table_type: "standard" | "always_free" | "gpt";
    }) => {
      const response = await post("/pricing-tables", data);
      return response.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["pricing-tables"] });
      onOpenChange(false);
      setPricingTableId("");
      setPricingTableType("standard");
      toast({
        title: "Success",
        description: "Pricing table updated successfully",
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

  const handleUpdate = () => {
    if (!pricingTableId.trim()) return;
    updatePricingTableMutation.mutate({
      pricing_table_id: pricingTableId,
      table_type: pricingTableType,
    });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogTrigger asChild>
        <Button variant="outline">Add Pricing Table</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add Pricing Table</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <div>
            <Label htmlFor="pricing-table-type">Table Type</Label>
            <Select
              value={pricingTableType}
              onValueChange={(value: "standard" | "always_free" | "gpt") =>
                setPricingTableType(value)
              }
            >
              <SelectTrigger>
                <SelectValue placeholder="Select table type" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="standard">Standard</SelectItem>
                <SelectItem value="always_free">Always Free</SelectItem>
                <SelectItem value="gpt">GPT</SelectItem>
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
          <div className="flex justify-end">
            <Button
              onClick={handleUpdate}
              disabled={
                updatePricingTableMutation.isPending || !pricingTableId.trim()
              }
            >
              {updatePricingTableMutation.isPending && (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              )}
              Add Pricing Table
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
