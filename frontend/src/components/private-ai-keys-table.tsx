"use client";

import { Eye, EyeOff, ArrowUp, ArrowDown, ArrowUpDown } from "lucide-react";
import { useState } from "react";
import { PrivateAIKeySpendCell } from "@/components/private-ai-key-spend-cell";
import { Button } from "@/components/ui/button";
import { DeleteConfirmationDialog } from "@/components/ui/delete-confirmation-dialog";
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
import { TableFilters, FilterField } from "@/components/ui/table-filters";
import { PrivateAIKey } from "@/types/private-ai-key";
import { User } from "@/types/user";

type SortField = "name" | "region" | "owner" | null;
type SortDirection = "asc" | "desc";
type KeyType = "full" | "llm" | "vector" | "all";

interface PrivateAIKeysTableProps {
  keys: PrivateAIKey[];
  onDelete: (keyId: number) => void;
  isLoading?: boolean;
  showOwner?: boolean;
  allowModification?: boolean;
  onUpdateBudget?: (keyId: number, budgetDuration: string) => void;
  isDeleting?: boolean;
  isUpdatingBudget?: boolean;
  teamDetails?: Record<number, { name: string }>;
  teamMembers?: User[];
}

export function PrivateAIKeysTable({
  keys,
  onDelete,
  isLoading = false,
  showOwner = false,
  allowModification = false,
  onUpdateBudget,
  isDeleting = false,
  isUpdatingBudget = false,
  teamDetails = {},
  teamMembers = [],
}: PrivateAIKeysTableProps) {
  const [showPassword, setShowPassword] = useState<
    Record<number | string, boolean>
  >({});
  const [sortField, setSortField] = useState<SortField>(null);
  const [sortDirection, setSortDirection] = useState<SortDirection>("asc");
  const [keyTypeFilter, setKeyTypeFilter] = useState<KeyType>("all");
  const [nameFilter, setNameFilter] = useState("");
  const [regionFilter, setRegionFilter] = useState("");
  const [databaseNameFilter, setDatabaseNameFilter] = useState("");
  const [ownerFilter, setOwnerFilter] = useState("");

  const togglePasswordVisibility = (keyId: number | string) => {
    setShowPassword((prev) => ({
      ...prev,
      [keyId]: !prev[keyId],
    }));
  };

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDirection(sortDirection === "asc" ? "desc" : "asc");
    } else {
      setSortField(field);
      setSortDirection("asc");
    }
  };

  const clearFilters = () => {
    setNameFilter("");
    setRegionFilter("");
    setDatabaseNameFilter("");
    setOwnerFilter("");
    setKeyTypeFilter("all");
  };

  const getSortedAndFilteredKeys = () => {
    let filteredKeys = keys;

    // Apply name filter
    if (nameFilter.trim()) {
      filteredKeys = filteredKeys.filter((key) =>
        key.name?.toLowerCase().includes(nameFilter.toLowerCase()),
      );
    }

    // Apply region filter
    if (regionFilter.trim()) {
      filteredKeys = filteredKeys.filter((key) =>
        key.region?.toLowerCase().includes(regionFilter.toLowerCase()),
      );
    }

    // Apply database name or username filter
    if (databaseNameFilter.trim()) {
      filteredKeys = filteredKeys.filter((key) =>
        key.database_name
          ?.toLowerCase()
          .includes(databaseNameFilter.toLowerCase()) ||
        key.database_username
          ?.toLowerCase()
          .includes(databaseNameFilter.toLowerCase())
      );
    }

    // Apply owner filter
    if (ownerFilter.trim()) {
      filteredKeys = filteredKeys.filter((key) => {
        if (key.owner_id) {
          const owner = teamMembers.find(
            (member) => member.id.toString() === key.owner_id?.toString(),
          );
          const ownerEmail = owner?.email || `User ${key.owner_id}`;
          return ownerEmail.toLowerCase().includes(ownerFilter.toLowerCase());
        } else if (key.team_id) {
          const teamName = teamDetails[key.team_id]?.name || "Team (Shared)";
          return teamName.toLowerCase().includes(ownerFilter.toLowerCase());
        }
        return false;
      });
    }

    // Apply key type filter
    if (keyTypeFilter !== "all") {
      filteredKeys = filteredKeys.filter((key) => {
        if (keyTypeFilter === "full") {
          return key.litellm_token && key.database_name;
        } else if (keyTypeFilter === "llm") {
          return key.litellm_token && !key.database_name;
        } else if (keyTypeFilter === "vector") {
          return !key.litellm_token && key.database_name;
        }
        return true;
      });
    }

    // Apply sorting
    if (sortField) {
      filteredKeys.sort((a, b) => {
        let aValue: string | number = "";
        let bValue: string | number = "";

        if (sortField === "name") {
          aValue = a.name || "";
          bValue = b.name || "";
        } else if (sortField === "region") {
          aValue = a.region || "";
          bValue = b.region || "";
        } else if (sortField === "owner") {
          if (a.owner_id) {
            const owner = teamMembers.find(
              (member) => member.id.toString() === a.owner_id?.toString(),
            );
            aValue = owner?.email || `User ${a.owner_id}`;
          } else if (a.team_id) {
            aValue = `(Team) ${teamDetails[a.team_id]?.name || "Team (Shared)"}`;
          }
          if (b.owner_id) {
            const owner = teamMembers.find(
              (member) => member.id.toString() === b.owner_id?.toString(),
            );
            bValue = owner?.email || `User ${b.owner_id}`;
          } else if (b.team_id) {
            bValue = `(Team) ${teamDetails[b.team_id]?.name || "Team (Shared)"}`;
          }
        }

        if (sortDirection === "asc") {
          return aValue > bValue ? 1 : -1;
        } else {
          return aValue < bValue ? 1 : -1;
        }
      });
    }

    return filteredKeys;
  };

  const hasActiveFilters = Boolean(
    nameFilter.trim() ||
    regionFilter.trim() ||
    databaseNameFilter.trim() ||
    ownerFilter.trim() ||
    keyTypeFilter !== "all",
  );

  // Filter fields configuration
  const filterFields: FilterField[] = [
    {
      key: "name",
      label: "Filter by Name",
      type: "search",
      placeholder: "Filter by name...",
      value: nameFilter,
      onChange: setNameFilter,
    },
    {
      key: "region",
      label: "Filter by Region",
      type: "search",
      placeholder: "Filter by region...",
      value: regionFilter,
      onChange: setRegionFilter,
    },
    {
      key: "databaseName",
      label: "Filter by DB Name or Username",
      type: "search",
      placeholder: "Filter by DB name or username...",
      value: databaseNameFilter,
      onChange: setDatabaseNameFilter,
    },
    {
      key: "owner",
      label: "Filter by Owner",
      type: "search",
      placeholder: "Filter by domain or email...",
      value: ownerFilter,
      onChange: setOwnerFilter,
    },
    {
      key: "type",
      label: "Filter by Type",
      type: "select",
      placeholder: "Filter by type",
      value: keyTypeFilter,
      onChange: (value: string) => setKeyTypeFilter(value as KeyType),
      options: [
        { value: "all", label: "All Keys" },
        { value: "full", label: "Full Keys" },
        { value: "llm", label: "LLM Only" },
        { value: "vector", label: "Vector DB Only" },
      ],
    },
  ];

  // Get filtered and sorted keys
  const filteredAndSortedKeys = getSortedAndFilteredKeys();

  // Pagination
  const {
    currentPage,
    pageSize,
    totalPages,
    totalItems,
    paginatedData,
    goToPage,
    changePageSize,
  } = useTablePagination(filteredAndSortedKeys, 10);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <TableFilters
        filters={filterFields}
        onClearFilters={clearFilters}
        hasActiveFilters={hasActiveFilters}
        totalItems={keys.length}
        filteredItems={filteredAndSortedKeys.length}
      />
      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>
                <Button
                  variant="ghost"
                  onClick={() => handleSort("name")}
                  className="flex items-center gap-1"
                >
                  Name
                  {sortField === "name" ? (
                    sortDirection === "asc" ? (
                      <ArrowUp className="h-4 w-4" />
                    ) : (
                      <ArrowDown className="h-4 w-4" />
                    )
                  ) : (
                    <ArrowUpDown className="h-4 w-4 opacity-50" />
                  )}
                </Button>
              </TableHead>
              <TableHead>Database Credentials</TableHead>
              <TableHead>LLM Credentials</TableHead>
              <TableHead>
                <Button
                  variant="ghost"
                  onClick={() => handleSort("region")}
                  className="flex items-center gap-1"
                >
                  Region
                  {sortField === "region" ? (
                    sortDirection === "asc" ? (
                      <ArrowUp className="h-4 w-4" />
                    ) : (
                      <ArrowDown className="h-4 w-4" />
                    )
                  ) : (
                    <ArrowUpDown className="h-4 w-4 opacity-50" />
                  )}
                </Button>
              </TableHead>
              {showOwner && (
                <TableHead>
                  <Button
                    variant="ghost"
                    onClick={() => handleSort("owner")}
                    className="flex items-center gap-1"
                  >
                    Owner
                    {sortField === "owner" ? (
                      sortDirection === "asc" ? (
                        <ArrowUp className="h-4 w-4" />
                      ) : (
                        <ArrowDown className="h-4 w-4" />
                      )
                    ) : (
                      <ArrowUpDown className="h-4 w-4 opacity-50" />
                    )}
                  </Button>
                </TableHead>
              )}
              <TableHead>Spend</TableHead>
              {allowModification && <TableHead>Actions</TableHead>}
            </TableRow>
          </TableHeader>
          <TableBody>
            {paginatedData.map((key, index) => (
              <TableRow key={key.id || `key-${index}`}>
                <TableCell>{key.name}</TableCell>
                <TableCell>
                  <div className="space-y-2">
                    {key.database_name ? (
                      <>
                        <div className="flex items-center gap-2">
                          <span>Database: {key.database_name}</span>
                        </div>
                        <div className="flex items-center gap-2">
                          <span>Host: {key.database_host}</span>
                        </div>
                        <div className="flex items-center gap-2">
                          <span>Username: {key.database_username}</span>
                        </div>
                        <div className="flex items-center gap-2">
                          <span>Password: </span>
                          <span className="font-mono">
                            {showPassword[key.id || `key-${index}`]
                              ? key.database_password
                              : "••••••••"}
                          </span>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() =>
                              togglePasswordVisibility(key.id || `key-${index}`)
                            }
                          >
                            {showPassword[key.id || `key-${index}`] ? (
                              <EyeOff className="h-4 w-4" />
                            ) : (
                              <Eye className="h-4 w-4" />
                            )}
                          </Button>
                        </div>
                      </>
                    ) : (
                      <span className="text-muted-foreground">
                        No Vector DB
                      </span>
                    )}
                  </div>
                </TableCell>
                <TableCell>
                  {key.litellm_token ? (
                    <div className="space-y-2">
                      <div className="flex items-center gap-2">
                        <span>Token: </span>
                        <span className="font-mono">
                          {showPassword[`${key.id}-token`]
                            ? key.litellm_token
                            : "••••••••"}
                        </span>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() =>
                            togglePasswordVisibility(`${key.id}-token`)
                          }
                        >
                          {showPassword[`${key.id}-token`] ? (
                            <EyeOff className="h-4 w-4" />
                          ) : (
                            <Eye className="h-4 w-4" />
                          )}
                        </Button>
                      </div>
                      {key.litellm_api_url && (
                        <div className="flex items-center gap-2">
                          <span>API URL: {key.litellm_api_url}</span>
                        </div>
                      )}
                    </div>
                  ) : (
                    <span className="text-muted-foreground">
                      No LLM credentials
                    </span>
                  )}
                </TableCell>
                <TableCell>{key.region}</TableCell>
                {showOwner && (
                  <TableCell>
                    <div className="flex flex-col gap-1">
                      {key.owner_id ? (
                        <span className="text-sm">
                          {teamMembers.find(
                            (member) =>
                              member.id.toString() === key.owner_id?.toString(),
                          )?.email || `User ${key.owner_id}`}
                        </span>
                      ) : key.team_id ? (
                        <span className="text-sm">
                          (Team){" "}
                          {teamDetails[key.team_id]?.name || "Team (Shared)"}
                        </span>
                      ) : null}
                    </div>
                  </TableCell>
                )}
                <TableCell>
                  <PrivateAIKeySpendCell
                    keyId={key.id}
                    hasLiteLLMToken={!!key.litellm_token}
                    allowModification={allowModification}
                    onUpdateBudget={onUpdateBudget}
                    isUpdatingBudget={isUpdatingBudget}
                  />
                </TableCell>
                {allowModification && (
                  <TableCell>
                    <DeleteConfirmationDialog
                      title="Delete Private AI Key"
                      description="Are you sure you want to delete this private AI key? This action cannot be undone."
                      onConfirm={() => onDelete(key.id)}
                      isLoading={isDeleting}
                    />
                  </TableCell>
                )}
              </TableRow>
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
    </div>
  );
}
