"use client";

import { Loader2, Users } from "lucide-react";
import { useState, Fragment } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
import { TableActionButtons } from "@/components/ui/table-action-buttons";
import { useToast } from "@/hooks/use-toast";
import { Region } from "@/types/region";
import { Team } from "@/types/team";
import { get, del } from "@/utils/api";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { CreateRegionDialog } from "./_components/create-region-dialog";
import { EditRegionDialog } from "./_components/edit-region-dialog";
import { ManageRegionTeamsDialog } from "./_components/manage-region-teams-dialog";

export default function RegionsPage() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [isAddingRegion, setIsAddingRegion] = useState(false);
  const [isEditingRegion, setIsEditingRegion] = useState(false);
  const [isManagingTeams, setIsManagingTeams] = useState(false);
  const [editingRegion, setEditingRegion] = useState<Region | null>(null);
  const [selectedRegionForTeams, setSelectedRegionForTeams] =
    useState<Region | null>(null);

  // Queries
  const { data: regions = [], isLoading: isLoadingRegions } = useQuery<
    Region[]
  >({
    queryKey: ["regions"],
    queryFn: async () => {
      const response = await get("regions/admin");
      return response.json();
    },
  });

  const { data: teams = [] } = useQuery<Team[]>({
    queryKey: ["teams"],
    queryFn: async () => {
      const response = await get("teams");
      return response.json();
    },
  });

  // Mutations
  const deleteRegionMutation = useMutation({
    mutationFn: async (regionId: string | number) => {
      await del(`regions/${regionId}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["regions"] });
      toast({
        title: "Success",
        description: "Region deleted successfully",
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

  const handleEditRegion = (region: Region) => {
    setEditingRegion(region);
    setIsEditingRegion(true);
  };

  const handleManageTeams = (region: Region) => {
    setSelectedRegionForTeams(region);
    setIsManagingTeams(true);
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
  } = useTablePagination(regions, 10);

  return (
    <div className="space-y-4">
      {isLoadingRegions ? (
        <div className="flex items-center justify-center min-h-[400px]">
          <Loader2 className="h-8 w-8 animate-spin" />
        </div>
      ) : (
        <>
          <div className="flex items-center justify-between">
            <h1 className="text-3xl font-bold">Regions</h1>
            <CreateRegionDialog
              open={isAddingRegion}
              onOpenChange={setIsAddingRegion}
            />
          </div>
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Label</TableHead>
                  <TableHead>Postgres Host</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Teams</TableHead>
                  <TableHead>Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {paginatedData.map((region) => (
                  <Fragment key={region.id}>
                    <TableRow className="border-b-0">
                      <TableCell>{region.name}</TableCell>
                      <TableCell>{region.label}</TableCell>
                      <TableCell>{region.postgres_host}</TableCell>
                      <TableCell>
                        <Badge
                          variant={
                            region.is_dedicated ? "default" : "secondary"
                          }
                        >
                          {region.is_dedicated ? "Dedicated" : "Shared"}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <span
                          className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                            region.is_active
                              ? "bg-green-100 text-green-800"
                              : "bg-red-100 text-red-800"
                          }`}
                        >
                          {region.is_active ? "Active" : "Inactive"}
                        </span>
                      </TableCell>
                      <TableCell>
                        {region.is_dedicated ? (
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => handleManageTeams(region)}
                            className="flex items-center gap-2"
                          >
                            <Users className="h-4 w-4" />
                            Manage Teams
                          </Button>
                        ) : (
                          <span className="text-gray-500 text-sm">N/A</span>
                        )}
                      </TableCell>
                      <TableCell>
                        <TableActionButtons
                          onEdit={() => handleEditRegion(region)}
                          onDelete={() =>
                            deleteRegionMutation.mutate(region.id)
                          }
                          deleteTitle="Delete Region"
                          deleteDescription="Are you sure you want to delete this region? This action cannot be undone."
                          isDeleting={deleteRegionMutation.isPending}
                        />
                      </TableCell>
                    </TableRow>
                    <TableRow>
                      <TableCell
                        colSpan={6}
                        className="pt-0 text-xs text-muted-foreground"
                      >
                        {region.description}
                      </TableCell>
                    </TableRow>
                  </Fragment>
                ))}
              </TableBody>
            </Table>
          </div>

          <TablePagination
            currentPage={currentPage}
            totalPages={totalPages}
            pageSize={pageSize}
            totalItems={totalItems}
            onPageChange={goToPage}
            onPageSizeChange={changePageSize}
          />

          <EditRegionDialog
            region={editingRegion}
            open={isEditingRegion}
            onOpenChange={setIsEditingRegion}
          />

          <ManageRegionTeamsDialog
            region={selectedRegionForTeams}
            open={isManagingTeams}
            onOpenChange={setIsManagingTeams}
            allTeams={teams}
          />
        </>
      )}
    </div>
  );
}
