"use client";

import {
  Loader2,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  ChevronsUpDown,
} from "lucide-react";
import { useState, useMemo, useEffect, Fragment } from "react";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  TablePagination,
} from "@/components/ui/table";
import { TableFilters, FilterField } from "@/components/ui/table-filters";
import { useDebounce } from "@/hooks/use-debounce";
import { Region } from "@/types/region";
import { Team } from "@/types/team";
import { get } from "@/utils/api";
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { AddUserToTeamDialog } from "./_components/add-user-to-team-dialog";
import { CreateTeamDialog } from "./_components/create-team-dialog";
import { CreateUserInTeamDialog } from "./_components/create-user-in-team-dialog";
import { EditTeamDialog } from "./_components/edit-team-dialog";
import { MergeTeamsDialog } from "./_components/merge-teams-dialog";
import { TeamExpansionRow } from "./_components/team-expansion-row";

type SortField = "name" | "admin_email" | "is_active" | "created_at" | null;
type SortDirection = "asc" | "desc";

export default function TeamsPage() {
  const [expandedTeamId, setExpandedTeamId] = useState<string | null>(null);
  const [includeDeleted, setIncludeDeleted] = useState(false);

  // Dialog states
  const [isAddingTeam, setIsAddingTeam] = useState(false);
  const [isMergingTeams, setIsMergingTeams] = useState(false);
  const [isEditingTeam, setIsEditingTeam] = useState(false);
  const [isAddingUserToTeam, setIsAddingUserToTeam] = useState(false);
  const [isCreatingUserInTeam, setIsCreatingUserInTeam] = useState(false);

  const [selectedTeamId, setSelectedTeamId] = useState<string | null>(null);
  const [editingTeam, setEditingTeam] = useState<Team | null>(null);

  // Filter and sort state
  const [nameFilter, setNameFilter] = useState("");
  const [adminEmailFilter, setAdminEmailFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [sortField, setSortField] = useState<SortField>(null);
  const [sortDirection, setSortDirection] = useState<SortDirection>("asc");

  // Server-side pagination state
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const debouncedNameFilter = useDebounce(nameFilter, 300);
  const debouncedAdminEmailFilter = useDebounce(adminEmailFilter, 300);

  // Filtering, sorting, and pagination happen server-side; loading every
  // team into the browser doesn't scale on production.
  const queryParams = useMemo(() => {
    const params = new URLSearchParams({
      include_deleted: includeDeleted.toString(),
      skip: ((currentPage - 1) * pageSize).toString(),
      limit: pageSize.toString(),
    });
    if (debouncedNameFilter) params.set("name", debouncedNameFilter);
    if (debouncedAdminEmailFilter)
      params.set("admin_email", debouncedAdminEmailFilter);
    if (statusFilter !== "all")
      params.set("is_active", (statusFilter === "active").toString());
    if (sortField) {
      params.set("sort_by", sortField);
      params.set("sort_order", sortDirection);
    }
    return params.toString();
  }, [
    includeDeleted,
    currentPage,
    pageSize,
    debouncedNameFilter,
    debouncedAdminEmailFilter,
    statusFilter,
    sortField,
    sortDirection,
  ]);

  // Keyed under ["teams", ...] so the useTeams mutations' invalidation
  // refreshes this list too.
  const { data: teamsData, isLoading: isLoadingTeams } = useQuery<{
    items: Team[];
    total: number;
  }>({
    queryKey: ["teams", "list", queryParams],
    queryFn: async () => {
      const response = await get(`/teams?${queryParams}`);
      const items: Team[] = await response.json();
      const total = Number(
        response.headers.get("X-Total-Count") ?? items.length,
      );
      return { items, total };
    },
    placeholderData: keepPreviousData,
  });
  const teams = teamsData?.items ?? [];
  const totalItems = teamsData?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(totalItems / pageSize));

  // Back to the first page whenever the result set changes shape
  useEffect(() => {
    setCurrentPage(1);
  }, [
    debouncedNameFilter,
    debouncedAdminEmailFilter,
    statusFilter,
    includeDeleted,
    sortField,
    sortDirection,
    pageSize,
  ]);

  // Clamp when mutations shrink the result set below the current page
  useEffect(() => {
    if (currentPage > totalPages) setCurrentPage(totalPages);
  }, [currentPage, totalPages]);

  const { data: regions = [] } = useQuery<Region[]>({
    queryKey: ["regions"],
    queryFn: async () => {
      const response = await get("/regions");
      return response.json();
    },
  });

  const hasActiveFilters = Boolean(
    nameFilter.trim() || adminEmailFilter.trim() || statusFilter !== "all",
  );

  const filterFields: FilterField[] = [
    {
      key: "name",
      label: "Filter by Name",
      type: "search",
      placeholder: "Search by team name...",
      value: nameFilter,
      onChange: setNameFilter,
    },
    {
      key: "adminEmail",
      label: "Filter by Admin Email",
      type: "search",
      placeholder: "Search by admin email...",
      value: adminEmailFilter,
      onChange: setAdminEmailFilter,
    },
    {
      key: "status",
      label: "Filter by Status",
      type: "select",
      placeholder: "All statuses",
      value: statusFilter,
      onChange: setStatusFilter,
      options: [
        { value: "all", label: "All statuses" },
        { value: "active", label: "Active" },
        { value: "inactive", label: "Inactive" },
      ],
    },
  ];

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDirection(sortDirection === "asc" ? "desc" : "asc");
    } else {
      setSortField(field);
      setSortDirection("asc");
    }
  };

  const getSortIcon = (field: SortField) => {
    if (sortField !== field) return <ChevronsUpDown className="h-4 w-4" />;
    return sortDirection === "asc" ? (
      <ChevronUp className="h-4 w-4" />
    ) : (
      <ChevronDown className="h-4 w-4" />
    );
  };

  const isTeamExpired = (team: Team): boolean => {
    if (team.is_always_free) return false;
    if (team.products && team.products.some((product) => product.active))
      return false;
    const thirtyDaysAgo = new Date();
    thirtyDaysAgo.setDate(thirtyDaysAgo.getDate() - 30);
    return !team.last_payment
      ? new Date(team.created_at) < thirtyDaysAgo
      : new Date(team.last_payment) < thirtyDaysAgo;
  };

  return (
    <div className="container mx-auto py-10">
      <div className="space-y-4">
        <div className="flex justify-between items-center">
          <h1 className="text-3xl font-bold">Teams</h1>
          <div className="flex space-x-2">
            <MergeTeamsDialog
              open={isMergingTeams}
              onOpenChange={setIsMergingTeams}
            />
            <CreateTeamDialog
              open={isAddingTeam}
              onOpenChange={setIsAddingTeam}
              regions={regions}
            />
          </div>
        </div>

        <TableFilters
          filters={filterFields}
          onClearFilters={() => {
            setNameFilter("");
            setAdminEmailFilter("");
            setStatusFilter("all");
            setSortField(null);
            setSortDirection("asc");
          }}
          hasActiveFilters={hasActiveFilters}
          totalItems={totalItems}
          filteredItems={totalItems}
        />

        <div className="flex items-center space-x-2 py-2">
          <Switch
            id="include-deleted"
            checked={includeDeleted}
            onCheckedChange={setIncludeDeleted}
          />
          <Label htmlFor="include-deleted" className="cursor-pointer">
            Show deleted teams
          </Label>
        </div>

        {isLoadingTeams ? (
          <div className="flex justify-center items-center h-64">
            <Loader2 className="h-8 w-8 animate-spin" />
          </div>
        ) : (
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-10"></TableHead>
                  <TableHead
                    className="cursor-pointer hover:bg-gray-50"
                    onClick={() => handleSort("name")}
                  >
                    <div className="flex items-center gap-2">
                      Name{getSortIcon("name")}
                    </div>
                  </TableHead>
                  <TableHead
                    className="cursor-pointer hover:bg-gray-50"
                    onClick={() => handleSort("admin_email")}
                  >
                    <div className="flex items-center gap-2">
                      Admin Email{getSortIcon("admin_email")}
                    </div>
                  </TableHead>
                  <TableHead
                    className="cursor-pointer hover:bg-gray-50"
                    onClick={() => handleSort("is_active")}
                  >
                    <div className="flex items-center gap-2">
                      Status{getSortIcon("is_active")}
                    </div>
                  </TableHead>
                  <TableHead
                    className="cursor-pointer hover:bg-gray-50"
                    onClick={() => handleSort("created_at")}
                  >
                    <div className="flex items-center gap-2">
                      Created At{getSortIcon("created_at")}
                    </div>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {teams.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={5} className="text-center py-6">
                      No teams found. Create a new team to get started.
                    </TableCell>
                  </TableRow>
                ) : (
                  teams.map((team) => (
                    <Fragment key={team.id}>
                      <TableRow
                        className={`cursor-pointer hover:bg-muted/50 ${team.deleted_at ? "opacity-50" : ""}`}
                        onClick={() =>
                          setExpandedTeamId(
                            expandedTeamId === team.id ? null : team.id,
                          )
                        }
                      >
                        <TableCell>
                          {expandedTeamId === team.id ? (
                            <ChevronDown className="h-4 w-4" />
                          ) : (
                            <ChevronRight className="h-4 w-4" />
                          )}
                        </TableCell>
                        <TableCell className="font-medium">
                          {team.name}
                        </TableCell>
                        <TableCell>{team.admin_email}</TableCell>
                        <TableCell>
                          <div className="flex flex-col gap-1">
                            {team.deleted_at ? (
                              <span className="inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium bg-gray-800 text-white">
                                DELETED
                              </span>
                            ) : (
                              <>
                                <span
                                  className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${team.is_active ? "bg-green-100 text-green-800" : "bg-red-100 text-red-800"}`}
                                >
                                  {team.is_active ? "Active" : "Inactive"}
                                </span>
                                {isTeamExpired(team) && (
                                  <span className="inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium bg-red-600 text-white">
                                    Expired
                                  </span>
                                )}
                              </>
                            )}
                          </div>
                        </TableCell>
                        <TableCell>
                          {new Date(team.created_at).toLocaleDateString()}
                        </TableCell>
                      </TableRow>
                      <TeamExpansionRow
                        teamId={team.id}
                        isExpanded={expandedTeamId === team.id}
                        includeDeleted={includeDeleted}
                        onEdit={(t) => {
                          setEditingTeam(t);
                          setIsEditingTeam(true);
                        }}
                        onAddUser={(id) => {
                          setSelectedTeamId(id);
                          setIsAddingUserToTeam(true);
                        }}
                        onCreateUser={(id) => {
                          setSelectedTeamId(id);
                          setIsCreatingUserInTeam(true);
                        }}
                        regions={regions}
                      />
                    </Fragment>
                  ))
                )}
              </TableBody>
            </Table>
          </div>
        )}

        <TablePagination
          currentPage={currentPage}
          totalPages={totalPages}
          pageSize={pageSize}
          totalItems={totalItems}
          onPageChange={setCurrentPage}
          onPageSizeChange={setPageSize}
        />

        <EditTeamDialog
          team={editingTeam}
          open={isEditingTeam}
          onOpenChange={setIsEditingTeam}
        />
        <AddUserToTeamDialog
          teamId={selectedTeamId}
          open={isAddingUserToTeam}
          onOpenChange={setIsAddingUserToTeam}
        />
        <CreateUserInTeamDialog
          teamId={selectedTeamId}
          open={isCreatingUserInTeam}
          onOpenChange={setIsCreatingUserInTeam}
        />
      </div>
    </div>
  );
}
