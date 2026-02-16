import { ChevronDown, ChevronRight } from "lucide-react";
import Script from "next/script";
import React, { useState } from "react";
import { DeleteConfirmationDialog } from "@/components/ui/delete-confirmation-dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useToast } from "@/hooks/use-toast";
import { PricingTables } from "@/types/pricing-table";
import { del } from "@/utils/api";
import { useMutation, useQueryClient } from "@tanstack/react-query";

interface PricingTablesListProps {
  pricingTables: PricingTables | undefined;
}

export function PricingTablesList({ pricingTables }: PricingTablesListProps) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [expandedPricingTable, setExpandedPricingTable] = useState<
    string | null
  >(null);

  const deletePricingTableMutation = useMutation({
    mutationFn: async (tableType: string) => {
      await del(`/pricing-tables?table_type=${tableType}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["pricing-tables"] });
      toast({
        title: "Success",
        description: "Pricing table deleted successfully",
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

  const toggleExpansion = (tableType: string) => {
    setExpandedPricingTable(
      expandedPricingTable === tableType ? null : tableType,
    );
  };

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-12"></TableHead>
          <TableHead>Table Type</TableHead>
          <TableHead>Pricing Table ID</TableHead>
          <TableHead>Last Updated</TableHead>
          <TableHead>Actions</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {pricingTables?.tables &&
          Object.entries(pricingTables.tables).map(([tableType, table]) => (
            <React.Fragment key={tableType}>
              <TableRow>
                <TableCell>
                  <button
                    onClick={() => toggleExpansion(tableType)}
                    className="flex items-center justify-center w-6 h-6 rounded hover:bg-muted"
                  >
                    {expandedPricingTable === tableType ? (
                      <ChevronDown className="h-4 w-4" />
                    ) : (
                      <ChevronRight className="h-4 w-4" />
                    )}
                  </button>
                </TableCell>
                <TableCell className="font-medium">
                  {tableType.charAt(0).toUpperCase() +
                    tableType.slice(1).replace("_", " ")}
                </TableCell>
                <TableCell className="font-mono text-sm">
                  {table ? table.pricing_table_id : "Not set"}
                </TableCell>
                <TableCell>
                  {table ? new Date(table.updated_at).toLocaleString() : "-"}
                </TableCell>
                <TableCell>
                  {table && (
                    <DeleteConfirmationDialog
                      title="Delete Pricing Table"
                      description={`Are you sure you want to delete the ${tableType} pricing table? This action cannot be undone.`}
                      triggerText="Delete"
                      onConfirm={() =>
                        deletePricingTableMutation.mutate(tableType)
                      }
                      isLoading={deletePricingTableMutation.isPending}
                      size="sm"
                    />
                  )}
                </TableCell>
              </TableRow>
              {expandedPricingTable === tableType && table && (
                <TableRow>
                  <TableCell colSpan={5} className="p-0">
                    <div className="p-4 bg-muted/30">
                      <Script
                        src="https://js.stripe.com/v3/pricing-table.js"
                        strategy="afterInteractive"
                      />
                      {table.stripe_publishable_key && (
                        // @ts-expect-error - Stripe pricing table is a custom element
                        <stripe-pricing-table
                          pricing-table-id={table.pricing_table_id}
                          publishable-key={table.stripe_publishable_key}
                        />
                      )}
                    </div>
                  </TableCell>
                </TableRow>
              )}
            </React.Fragment>
          ))}
      </TableBody>
    </Table>
  );
}
