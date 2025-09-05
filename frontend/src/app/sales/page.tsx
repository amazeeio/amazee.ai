'use client';

import { useState, useMemo, useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  TablePagination,
} from '@/components/ui/table';

import { Loader2, ChevronUp, ChevronDown, ChevronsUpDown, DollarSign, Calendar, Users, Globe, Package, Plus, X } from 'lucide-react';
import { get } from '@/utils/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Input } from '@/components/ui/input';

interface Team {
  id: number;
  name: string;
  admin_email: string;
  created_at: string;
  last_payment?: string;
  is_always_free: boolean;
  products: Product[];
  regions: string[];
  total_spend: number;
  trial_status: string;
}

interface Product {
  id: string;
  name: string;
  active: boolean;
}



type SortField = 'admin_email' | 'name' | 'created_at' | 'last_payment' | 'products' | 'trial_status' | 'regions' | 'total_spend' | null;
type SortDirection = 'asc' | 'desc';

interface Filter {
  id: string;
  column: string;
  value: string;
  operator: 'contains' | 'equals' | 'starts_with' | 'ends_with';
}

export default function SalesPage() {

  // Filter and sort state
  const [filters, setFilters] = useState<Filter[]>([]);
  const [sortField, setSortField] = useState<SortField>(null);
  const [sortDirection, setSortDirection] = useState<SortDirection>('asc');


  // Queries
  const { data: teams = [], isLoading: isLoadingTeams } = useQuery<Team[]>({
    queryKey: ['sales-teams'],
    queryFn: async () => {
      const response = await get('/teams/sales/list-teams');
      const data = await response.json();
      return data.teams;
    },
  });




  // Calculate trial time remaining
  const getTrialTimeRemaining = useCallback((team: Team): string => {
    return team.trial_status;
  }, []);

  // Get unique regions for a team
  const getTeamRegions = useCallback((team: Team): string[] => {
    return team.regions;
  }, []);

  // Get total spend for a team
  const getTeamTotalSpend = useCallback((team: Team): number => {
    return team.total_spend;
  }, []);

  // Get all team spend values for calculating min/max
  const allTeamSpends = useMemo(() => {
    return teams.map(team => ({
      teamId: team.id,
      spend: getTeamTotalSpend(team)
    })).filter(item => item.spend > 0); // Only non-zero values for min calculation
  }, [teams, getTeamTotalSpend]);

  // Calculate min and max spend values
  const spendStats = useMemo(() => {
    if (allTeamSpends.length === 0) {
      return { minSpend: 0, maxSpend: 0 };
    }

    const spends = allTeamSpends.map(item => item.spend);
    return {
      minSpend: Math.min(...spends),
      maxSpend: Math.max(...spends)
    };
  }, [allTeamSpends]);


  // Get color for spend value based on gradient rules
  const getSpendColor = useCallback((spend: number): string => {
    if (spend === 0) {
      return '#6b7280'; // Dark grey for $0.00
    }

    if (spend === spendStats.maxSpend) {
      return '#166534'; // Dark green for highest value
    }

    if (spend === spendStats.minSpend) {
      return '#000000'; // Black for lowest non-zero value
    }

    // Gradient for values between min and max
    if (spendStats.maxSpend === spendStats.minSpend) {
      return '#166534'; // If all values are the same, use dark green
    }

    const ratio = (spend - spendStats.minSpend) / (spendStats.maxSpend - spendStats.minSpend);
    const red = Math.round(22 + (ratio * 0)); // Start from dark green (22, 101, 52)
    const green = Math.round(101 + (ratio * 53)); // End at darker green (22, 154, 52)
    const blue = Math.round(52 + (ratio * 0));

    return `rgb(${red}, ${green}, ${blue})`;
  }, [spendStats]);

  // Add a new filter
  const addFilter = () => {
    const newFilter: Filter = {
      id: Date.now().toString(),
      column: 'admin_email',
      value: '',
      operator: 'contains'
    };
    setFilters([...filters, newFilter]);
  };

  // Update filter when column changes to set appropriate default value
  const handleColumnChange = (filterId: string, column: string) => {
    const columnInfo = filterColumns.find(col => col.value === column);
    let defaultValue = '';
    let defaultOperator: Filter['operator'] = 'contains';

    if (columnInfo?.type === 'select') {
      const options = getFilterOptions(column);
      if (options.length > 0) {
        defaultValue = options[0].value;
      }
      // Regions use 'contains' since teams can have multiple regions
      if (column === 'regions') {
        defaultOperator = 'contains';
      } else {
        defaultOperator = 'equals';
      }
    } else if (columnInfo?.type === 'number') {
      defaultOperator = 'equals';
    }

    updateFilter(filterId, { column, value: defaultValue, operator: defaultOperator });
  };

  // Remove a filter
  const removeFilter = (filterId: string) => {
    setFilters(filters.filter(f => f.id !== filterId));
  };

  // Update a filter
  const updateFilter = (filterId: string, updates: Partial<Filter>) => {
    setFilters(filters.map(f =>
      f.id === filterId ? { ...f, ...updates } : f
    ));
  };

  // Clear all filters
  const clearAllFilters = () => {
    setFilters([]);
    setSortField(null);
    setSortDirection('asc');
  };


  // Available filter columns
  const filterColumns = [
    { value: 'admin_email', label: 'Team Email', type: 'text' },
    { value: 'name', label: 'Team Name', type: 'text' },
    { value: 'products', label: 'Products', type: 'select' },
    { value: 'trial_status', label: 'Trial Status', type: 'select' },
    { value: 'regions', label: 'Regions', type: 'select' },
  ];

  // Filter operators
  const filterOperators = [
    { value: 'contains', label: 'Contains' },
    { value: 'equals', label: 'Equals' },
    { value: 'starts_with', label: 'Starts with' },
    { value: 'ends_with', label: 'Ends with' },
  ];

  // Get operators for specific column types
  const getOperatorsForColumn = (column: string) => {
    const columnInfo = filterColumns.find(col => col.value === column);

    if (columnInfo?.type === 'select') {
      // Regions can use 'contains' since teams can have multiple regions
      if (column === 'regions') {
        return [
          { value: 'equals', label: 'Equals' },
          { value: 'contains', label: 'Contains' }
        ];
      }
      return [{ value: 'equals', label: 'Equals' }];
    }

    if (columnInfo?.type === 'number') {
      return [
        { value: 'equals', label: 'Equals' },
        { value: 'contains', label: 'Contains' }
      ];
    }

    // Text fields get all operators
    return filterOperators;
  };

  // Get options for select-type filters
  const getFilterOptions = (column: string) => {
    switch (column) {
      case 'trial_status':
        return [
          { value: 'Active Product', label: 'Active Product' },
          { value: 'Always Free', label: 'Always Free' },
          { value: 'In Progress', label: 'In Progress' },
          { value: 'Expired', label: 'Expired' },
        ];
      case 'regions':
        // Get unique regions from all teams
        const allRegions = new Set<string>();
        teams.forEach(team => {
          const teamRegions = getTeamRegions(team);
          teamRegions.forEach(region => allRegions.add(region));
        });
        return [
          { value: 'No Region', label: 'No Region' },
          ...Array.from(allRegions).sort().map(region => ({
            value: region,
            label: region
          }))
        ];
      case 'products':
        const allProducts = new Set<string>();
        teams.forEach(team => {
          const teamProducts = team.products || [];
          teamProducts.forEach(product => allProducts.add(product.name));
        });
        return [
          { value: 'No Product', label: 'No Product' },
          ...Array.from(allProducts).sort().map(productName => ({
            value: productName,
            label: productName
          }))
        ];
      default:
        return [];
    }
  };

  // Get filter input component based on column type
  const getFilterInput = (filter: Filter) => {
    const column = filterColumns.find(col => col.value === filter.column);

    if (column?.type === 'select') {
      const options = getFilterOptions(filter.column);
      return (
        <Select
          value={filter.value}
          onValueChange={(value) => updateFilter(filter.id, { value })}
        >
          <SelectTrigger className="flex-1">
            <SelectValue placeholder="Select value..." />
          </SelectTrigger>
          <SelectContent>
            {options.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      );
    }

    if (column?.type === 'number') {
      return (
        <Input
          type="number"
          placeholder="Enter value..."
          value={filter.value}
          onChange={(e) => updateFilter(filter.id, { value: e.target.value })}
          className="flex-1"
        />
      );
    }

    // Default to text input
    return (
      <Input
        placeholder="Enter value..."
        value={filter.value}
        onChange={(e) => updateFilter(filter.id, { value: e.target.value })}
        className="flex-1"
      />
    );
  };

  // Apply filters to teams
  const applyFilters = useCallback((teams: Team[]) => {
    if (filters.length === 0) return teams;

    return teams.filter(team => {
      return filters.every(filter => {
        let teamValue: string | number;

        switch (filter.column) {
          case 'admin_email':
            teamValue = team.admin_email.toLowerCase();
            break;
          case 'name':
            teamValue = team.name.toLowerCase();
            break;
                     case 'products':
             const teamProducts = team.products || [];
             teamValue = teamProducts.length > 0 ? teamProducts.map(p => p.name).join(', ') : 'No Product';
             break;
                     case 'trial_status':
             const trialStatus = getTrialTimeRemaining(team);
             if (trialStatus.includes('days left')) {
               teamValue = 'In Progress';
             } else {
               teamValue = trialStatus;
             }
             break;
                                case 'regions':
             const teamRegions = getTeamRegions(team);
             teamValue = teamRegions.length > 0 ? teamRegions.join(', ') : 'No Region';
             break;
          default:
            return true;
        }

        const filterValue = filter.value.toLowerCase();

        if (typeof teamValue === 'number') {
          // Handle numeric comparisons
          const numValue = parseFloat(filterValue);
          if (isNaN(numValue)) return true;
          return teamValue === numValue;
        }

        // Handle string comparisons
        switch (filter.operator) {
          case 'contains':
            return teamValue.toLowerCase().includes(filterValue);
          case 'equals':
            return teamValue.toLowerCase() === filterValue;
          case 'starts_with':
            return teamValue.toLowerCase().startsWith(filterValue);
          case 'ends_with':
            return teamValue.toLowerCase().endsWith(filterValue);
          default:
            return true;
        }
      });
    });
  }, [filters, getTrialTimeRemaining, getTeamRegions]);

  // Filtered and sorted teams
  const filteredAndSortedTeams = useMemo(() => {
    const filtered = applyFilters(teams);

    if (sortField) {
      filtered.sort((a, b) => {
        let aValue: string | number;
        let bValue: string | number;

        switch (sortField) {
          case 'admin_email':
            aValue = a.admin_email.toLowerCase();
            bValue = b.admin_email.toLowerCase();
            break;
          case 'name':
            aValue = a.name.toLowerCase();
            bValue = b.name.toLowerCase();
            break;
          case 'created_at':
            aValue = new Date(a.created_at).getTime();
            bValue = new Date(b.created_at).getTime();
            break;
          case 'last_payment':
            aValue = a.last_payment ? new Date(a.last_payment).getTime() : 0;
            bValue = b.last_payment ? new Date(b.last_payment).getTime() : 0;
            break;
          case 'products':
            aValue = (a.products || []).length;
            bValue = (b.products || []).length;
            break;
          case 'trial_status':
            aValue = getTrialTimeRemaining(a);
            bValue = getTrialTimeRemaining(b);
            break;
          case 'regions':
            aValue = getTeamRegions(a).length;
            bValue = getTeamRegions(b).length;
            break;
          case 'total_spend':
            aValue = getTeamTotalSpend(a);
            bValue = getTeamTotalSpend(b);
            break;
          default:
            return 0;
        }

        if (sortDirection === 'asc') {
          return aValue < bValue ? -1 : aValue > bValue ? 1 : 0;
        } else {
          return aValue > bValue ? -1 : aValue < bValue ? 1 : 0;
        }
      });
    }

    return filtered;
  }, [teams, sortField, sortDirection, getTrialTimeRemaining, getTeamRegions, getTeamTotalSpend, applyFilters]);

  const hasActiveFilters = filters.length > 0;

  // Manual pagination to ensure it updates with local state changes
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);

  const totalItems = filteredAndSortedTeams.length;
  const totalPages = Math.ceil(totalItems / pageSize);

  const startIndex = (currentPage - 1) * pageSize;
  const endIndex = startIndex + pageSize;
  const paginatedData = filteredAndSortedTeams.slice(startIndex, endIndex);

  const goToPage = (page: number) => setCurrentPage(page);
  const changePageSize = (newPageSize: number) => {
    setPageSize(newPageSize);
    setCurrentPage(1); // Reset to first page when changing page size
  };

  // Handle sorting
  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortDirection('asc');
    }
  };

  // Get sort icon
  const getSortIcon = (field: SortField) => {
    if (sortField !== field) {
      return <ChevronsUpDown className="h-4 w-4" />;
    }
    return sortDirection === 'asc' ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />;
  };

  // Format date
  const formatDate = (dateString: string | undefined): string => {
    if (!dateString) return 'Never';
    return new Date(dateString).toLocaleDateString();
  };

  // Format currency
  const formatCurrency = (amount: number): string => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 4,
      maximumFractionDigits: 4,
    }).format(amount);
  };

  if (isLoadingTeams) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <Loader2 className="h-8 w-8 animate-spin" />
        <div className="ml-3 text-sm text-muted-foreground">
          Loading teams...
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4 w-full min-w-0 px-4 sm:px-6 lg:px-8">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-3xl font-bold">Sales Dashboard</h1>
          <p className="text-muted-foreground mt-2">
            Monitor team performance, subscriptions, and revenue metrics
          </p>
        </div>
        <div className="flex items-center gap-4">
          <div className="text-right">
            <div className="text-2xl font-bold text-green-600">
              {teams.length}
            </div>
            <div className="text-sm text-muted-foreground">Total Teams</div>
          </div>
        </div>
      </div>

      {/* Filters Section */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-lg font-medium">Filters</h3>
          <div className="flex items-center gap-2">
            {hasActiveFilters && (
              <Button
                variant="outline"
                size="sm"
                onClick={clearAllFilters}
              >
                Clear All
              </Button>
            )}
            <Button
              variant="outline"
              size="sm"
              onClick={addFilter}
            >
              <Plus className="h-4 w-4 mr-2" />
              Add Filter
            </Button>
          </div>
        </div>

        {filters.length > 0 && (
          <div className="space-y-3">
            {filters.map((filter) => (
              <div key={filter.id} className="flex items-center gap-3 p-3 border rounded-lg bg-muted/30">
                                 <Select
                   value={filter.column}
                   onValueChange={(value) => handleColumnChange(filter.id, value)}
                 >
                  <SelectTrigger className="w-40">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {filterColumns.map((column) => (
                      <SelectItem key={column.value} value={column.value}>
                        {column.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>

                                 <Select
                   value={filter.operator}
                   onValueChange={(value) => updateFilter(filter.id, { operator: value as Filter['operator'] })}
                 >
                   <SelectTrigger className="w-32">
                     <SelectValue />
                   </SelectTrigger>
                   <SelectContent>
                     {getOperatorsForColumn(filter.column).map((op) => (
                       <SelectItem key={op.value} value={op.value}>
                         {op.label}
                       </SelectItem>
                     ))}
                   </SelectContent>
                 </Select>

                {getFilterInput(filter)}

                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => removeFilter(filter.id)}
                  className="text-muted-foreground hover:text-destructive"
                >
                  <X className="h-4 w-4" />
                </Button>
              </div>
            ))}
          </div>
        )}

        {hasActiveFilters && (
          <div className="text-sm text-muted-foreground">
            Showing {filteredAndSortedTeams.length} of {teams.length} teams
          </div>
        )}
      </div>

      <div className="rounded-md border w-full min-w-0">
        <div className="overflow-x-auto w-full">
          <Table className="w-full table-fixed text-xs">
          <TableHeader>
            <TableRow className="h-10">
              <TableHead
                className="cursor-pointer hover:bg-gray-50 w-36"
                onClick={() => handleSort('admin_email')}
              >
                <div className="flex items-center gap-2">
                  <Users className="h-4 w-4" />
                  Team Email
                  {getSortIcon('admin_email')}
                </div>
              </TableHead>
              <TableHead
                className="cursor-pointer hover:bg-gray-50 w-32"
                onClick={() => handleSort('name')}
              >
                <div className="flex items-center gap-2">
                  Team Name
                  {getSortIcon('name')}
                </div>
              </TableHead>
              <TableHead
                className="cursor-pointer hover:bg-gray-50 w-24"
                onClick={() => handleSort('created_at')}
              >
                <div className="flex items-center gap-2">
                  <Calendar className="h-4 w-4" />
                  Team Create Date
                  {getSortIcon('created_at')}
                </div>
              </TableHead>
              <TableHead
                className="cursor-pointer hover:bg-gray-50 w-24"
                onClick={() => handleSort('last_payment')}
              >
                <div className="flex items-center gap-2">
                  <Calendar className="h-4 w-4" />
                  Last Payment
                  {getSortIcon('last_payment')}
                </div>
              </TableHead>
              <TableHead
                className="cursor-pointer hover:bg-gray-50 w-28"
                onClick={() => handleSort('products')}
              >
                <div className="flex items-center gap-2">
                  <Package className="h-4 w-4" />
                  Products
                  {getSortIcon('products')}
                </div>
              </TableHead>
              <TableHead
                className="cursor-pointer hover:bg-gray-50 w-28"
                onClick={() => handleSort('trial_status')}
              >
                <div className="flex items-center gap-2">
                  <Calendar className="h-4 w-4" />
                  Trial Status
                  {getSortIcon('trial_status')}
                </div>
              </TableHead>
              <TableHead
                className="cursor-pointer hover:bg-gray-50 w-28"
                onClick={() => handleSort('regions')}
              >
                <div className="flex items-center gap-2">
                  <Globe className="h-4 w-4" />
                  Regions
                  {getSortIcon('regions')}
                </div>
              </TableHead>
              <TableHead
                className="cursor-pointer hover:bg-gray-50 w-24"
                onClick={() => handleSort('total_spend')}
              >
                <div className="flex items-center gap-2">
                  <DollarSign className="h-4 w-4" />
                  Total Spend
                  {getSortIcon('total_spend')}
                </div>
              </TableHead>

            </TableRow>
          </TableHeader>
          <TableBody>
            {paginatedData.length === 0 ? (
              <TableRow>
                <TableCell colSpan={9} className="text-center py-6">
                  No teams found matching your filters.
                </TableCell>
              </TableRow>
            ) : (
              paginatedData.map((team) => (
                <TableRow key={team.id} className="hover:bg-muted/50 h-12">
                  <TableCell className="font-medium py-2">
                    <div className="truncate">{team.admin_email}</div>
                  </TableCell>
                  <TableCell className="py-2">
                    <div className="font-medium truncate">{team.name}</div>
                    <div className="text-xs text-muted-foreground">
                      ID: {team.id}
                    </div>
                  </TableCell>
                  <TableCell className="py-2 text-xs">
                    {formatDate(team.created_at)}
                  </TableCell>
                  <TableCell className="py-2 text-xs">
                    {formatDate(team.last_payment)}
                  </TableCell>
                  <TableCell className="py-2">
                    {(() => {
                      const teamProducts = team.products || [];
                      return teamProducts.length > 0 ? (
                        <div className="space-y-0.5">
                          {teamProducts.map((product) => (
                            <Badge
                              key={product.id}
                              variant={product.active ? "default" : "secondary"}
                              className="mr-1 text-xs px-1.5 py-0.5"
                            >
                              {product.name}
                            </Badge>
                          ))}
                        </div>
                      ) : (
                        <span className="text-muted-foreground text-xs">No products</span>
                      );
                    })()}
                  </TableCell>
                  <TableCell className="py-2">
                    {(() => {
                      const trialStatus = getTrialTimeRemaining(team);
                      let badgeVariant: "default" | "secondary" | "destructive" | "outline" = "outline";
                      let customStyle = {};

                      if (trialStatus === 'Active Product') {
                        badgeVariant = "default";
                        customStyle = { backgroundColor: '#166534', color: 'white' }; // dark green
                      } else if (trialStatus === 'Always Free') {
                        badgeVariant = "secondary";
                      } else if (trialStatus === 'Expired') {
                        badgeVariant = "destructive";
                        customStyle = { backgroundColor: '#991b1b', color: 'white' }; // dark red
                      } else if (trialStatus === 'In Progress') {
                        badgeVariant = "outline";
                        customStyle = { backgroundColor: '#fef3c7', color: '#92400e' }; // amber/yellow
                      } else if (trialStatus.includes('days left')) {
                        // Extract the number of days
                        const daysMatch = trialStatus.match(/(\d+)/);
                        if (daysMatch) {
                          const days = parseInt(daysMatch[1], 10);
                          // Calculate color gradient: 30 days = green, 0 days = red
                          // Use a 30-day scale instead of 7-day for better gradient
                          const ratio = Math.max(0, Math.min(1, days / 30));

                          // Green to red gradient: green (34, 197, 94) to red (239, 68, 68)
                          const red = Math.round(34 + (205 * (1 - ratio))); // 34-239 range
                          const green = Math.round(197 - (129 * (1 - ratio))); // 197-68 range
                          const blue = Math.round(94 - (26 * (1 - ratio))); // 94-68 range

                          customStyle = {
                            backgroundColor: `rgb(${red}, ${green}, ${blue})`,
                            color: 'white',
                            fontWeight: 'bold'
                          };
                        }
                      }

                      return (
                        <Badge
                          variant={badgeVariant}
                          style={customStyle}
                          className="text-xs px-1.5 py-0.5"
                        >
                          {trialStatus}
                        </Badge>
                      );
                    })()}
                  </TableCell>
                  <TableCell className="py-2">
                    {(() => {
                      const regions = getTeamRegions(team);
                      if (regions.length === 0) {
                        return <span className="text-muted-foreground text-xs">No regions</span>;
                      }
                      return (
                        <div className="space-y-0.5">
                          {regions.map((region) => (
                            <Badge
                              key={region}
                              variant="outline"
                              className="mr-1 text-xs px-1.5 py-0.5"
                            >
                              {region}
                            </Badge>
                          ))}
                        </div>
                      );
                    })()}
                  </TableCell>
                  <TableCell className="py-2">
                    {(() => {
                      const totalSpend = getTeamTotalSpend(team);
                      const spendColor = getSpendColor(totalSpend);
                      return (
                        <div className="font-medium text-xs" style={{ color: spendColor }}>
                          {formatCurrency(totalSpend)}
                        </div>
                      );
                    })()}
                  </TableCell>

                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
        </div>
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
