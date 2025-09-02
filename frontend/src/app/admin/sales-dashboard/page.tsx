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
  useTablePagination,
} from '@/components/ui/table';

import { Loader2, ChevronUp, ChevronDown, ChevronsUpDown, DollarSign, Calendar, Users, Globe, Package, Plus, X } from 'lucide-react';
import { get } from '@/utils/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Input } from '@/components/ui/input';

interface Team {
  id: string;
  name: string;
  admin_email: string;
  created_at: string;
  last_payment?: string;
  is_always_free: boolean;
  hubspot_status?: string; // New field for tracking Hubspot import status - will use "update-sales-data" API after it has been built
}

interface Product {
  id: string;
  name: string;
  active: boolean;
}

interface PrivateAIKey {
  id: number;
  name: string;
  region: string;
  team_id?: number;
}

interface SpendInfo {
  spend: number;
  expires: string;
  created_at: string;
  updated_at: string;
  max_budget: number | null;
  budget_duration: string | null;
  budget_reset_at: string | null;
}


type SortField = 'admin_email' | 'name' | 'created_at' | 'last_payment' | 'products' | 'trial_status' | 'regions' | 'total_spend' | 'hubspot_status' | null;
type SortDirection = 'asc' | 'desc';

interface Filter {
  id: string;
  column: string;
  value: string;
  operator: 'contains' | 'equals' | 'starts_with' | 'ends_with';
}

export default function SalesDashboardPage() {

  // Filter and sort state
  const [filters, setFilters] = useState<Filter[]>([]);
  const [sortField, setSortField] = useState<SortField>(null);
  const [sortDirection, setSortDirection] = useState<SortDirection>('asc');

  // Local state for hubspot status updates (until API is implemented)
  const [localHubspotStatuses, setLocalHubspotStatuses] = useState<Record<string, string>>({});

  // Queries
  const { data: teams = [], isLoading: isLoadingTeams } = useQuery<Team[]>({
    queryKey: ['teams'],
    queryFn: async () => {
      const response = await get('/teams');
      const data = await response.json();
      return data;
    },
  });

  // Get products for all teams
  const { data: teamProductsMap = {} } = useQuery<Record<string, Product[]>>({
    queryKey: ['team-products-map'],
    queryFn: async () => {
      const productsMap: Record<string, Product[]> = {};

      for (const team of teams) {
        try {
          const response = await get(`/products?team_id=${team.id}`);
          const products = await response.json();
          productsMap[team.id] = products;
        } catch (error) {
          console.error(`Failed to fetch products for team ${team.id}:`, error);
          productsMap[team.id] = [];
        }
      }

      return productsMap;
    },
    enabled: teams.length > 0,
  });

  // Get AI keys for all teams
  const { data: teamAIKeysMap = {} } = useQuery<Record<string, PrivateAIKey[]>>({
    queryKey: ['team-ai-keys-map'],
    queryFn: async () => {
      const aiKeysMap: Record<string, PrivateAIKey[]> = {};

      for (const team of teams) {
        try {
          const response = await get(`/private-ai-keys?team_id=${team.id}`);
          const aiKeys = await response.json();
          aiKeysMap[team.id] = aiKeys;
        } catch (error) {
          console.error(`Failed to fetch AI keys for team ${team.id}:`, error);
          aiKeysMap[team.id] = [];
        }
      }

      return aiKeysMap;
    },
    enabled: teams.length > 0,
  });

  // Get spend data for all AI keys
  const { data: teamSpendMap = {} } = useQuery<Record<string, SpendInfo[]>>({
    queryKey: ['team-spend-map', teamAIKeysMap],
    queryFn: async () => {
      const spendMap: Record<string, SpendInfo[]> = {};

      for (const team of teams) {
        const teamKeys = teamAIKeysMap[team.id] || [];
        const teamSpend: SpendInfo[] = [];

        for (const key of teamKeys) {
          try {
            const response = await get(`/private-ai-keys/${key.id}/spend`);
            const spendInfo = await response.json();
            teamSpend.push(spendInfo);
          } catch (error) {
            console.error(`Failed to fetch spend data for key ${key.id}:`, error);
            // Add default spend info if fetch fails
            teamSpend.push({
              spend: 0,
              expires: '',
              created_at: '',
              updated_at: '',
              max_budget: null,
              budget_duration: null,
              budget_reset_at: null,
            });
          }
        }

        spendMap[team.id] = teamSpend;
      }

      return spendMap;
    },
    enabled: teams.length > 0 && Object.keys(teamAIKeysMap).length > 0,
  });



  // Calculate trial time remaining
  const getTrialTimeRemaining = useCallback((team: Team): string => {
    const teamProducts = teamProductsMap[team.id] || [];
    if (teamProducts.some(p => p.active)) {
      return 'Active Product';
    }

    if (team.is_always_free) {
      return 'Always Free';
    }

    const now = new Date();
    const createdAt = new Date(team.created_at);
    const lastPayment = team.last_payment ? new Date(team.last_payment) : null;

    if (lastPayment) {
      // If there's a last payment, calculate based on 30 days from last payment
      const thirtyDaysFromPayment = new Date(lastPayment);
      thirtyDaysFromPayment.setDate(thirtyDaysFromPayment.getDate() + 30);

      if (now > thirtyDaysFromPayment) {
        return 'Expired';
      }

      const diffTime = thirtyDaysFromPayment.getTime() - now.getTime();
      const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));
      return `${diffDays} days remaining`;
    } else {
      // If no last payment, calculate based on 30 days from creation
      const thirtyDaysFromCreation = new Date(createdAt);
      thirtyDaysFromCreation.setDate(thirtyDaysFromCreation.getDate() + 30);

      if (now > thirtyDaysFromCreation) {
        return 'Expired';
      }

      const diffTime = thirtyDaysFromCreation.getTime() - now.getTime();
      const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));
      return `${diffDays} days remaining`;
    }
  }, [teamProductsMap]);

  // Get unique regions for a team
  const getTeamRegions = useCallback((teamId: string): string[] => {
    const teamKeys = teamAIKeysMap[teamId] || [];
    const regions = new Set<string>();

    teamKeys.forEach(key => {
      if (key.region) {
        regions.add(key.region);
      }
    });

    return Array.from(regions).sort();
  }, [teamAIKeysMap]);

  // Get total spend for a team
  const getTeamTotalSpend = useCallback((teamId: string): number => {
    const teamSpend = teamSpendMap[teamId] || [];
    return teamSpend.reduce((total, spendInfo) => total + (spendInfo.spend || 0), 0);
  }, [teamSpendMap]);

  // Get all team spend values for calculating min/max
  const allTeamSpends = useMemo(() => {
    return teams.map(team => ({
      teamId: team.id,
      spend: getTeamTotalSpend(team.id)
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
    { value: 'hubspot_status', label: 'Hubspot Status', type: 'select' },
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
          const teamRegions = getTeamRegions(team.id);
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
          const teamProducts = teamProductsMap[team.id] || [];
          teamProducts.forEach(product => allProducts.add(product.name));
        });
        return [
          { value: 'No Product', label: 'No Product' },
          ...Array.from(allProducts).sort().map(productName => ({
            value: productName,
            label: productName
          }))
        ];
      case 'hubspot_status':
        return [
          { value: 'Needs Import', label: 'Needs Import' },
          { value: 'Import Successful', label: 'Import Successful' },
          { value: 'Import Failed', label: 'Import Failed' },
          { value: 'Skip Import', label: 'Skip Import' },
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
             const teamProducts = teamProductsMap[team.id] || [];
             teamValue = teamProducts.length > 0 ? teamProducts.map(p => p.name).join(', ') : 'No Product';
             break;
                     case 'trial_status':
             const trialStatus = getTrialTimeRemaining(team);
             if (trialStatus.includes('days remaining')) {
               teamValue = 'In Progress';
             } else {
               teamValue = trialStatus;
             }
             break;
                                case 'regions':
             const teamRegions = getTeamRegions(team.id);
             teamValue = teamRegions.length > 0 ? teamRegions.join(', ') : 'No Region';
             break;
                                case 'hubspot_status':
             teamValue = localHubspotStatuses[team.id] || team.hubspot_status || 'Needs Import';
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
  }, [filters, teamProductsMap, getTrialTimeRemaining, getTeamRegions, getTeamTotalSpend]);

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
            aValue = (teamProductsMap[a.id] || []).length;
            bValue = (teamProductsMap[b.id] || []).length;
            break;
          case 'trial_status':
            aValue = getTrialTimeRemaining(a);
            bValue = getTrialTimeRemaining(b);
            break;
          case 'regions':
            aValue = getTeamRegions(a.id).length;
            bValue = getTeamRegions(b.id).length;
            break;
          case 'total_spend':
            aValue = getTeamTotalSpend(a.id);
            bValue = getTeamTotalSpend(b.id);
            break;
          case 'hubspot_status':
            aValue = localHubspotStatuses[a.id] || a.hubspot_status || 'Needs Import';
            bValue = localHubspotStatuses[b.id] || b.hubspot_status || 'Needs Import';
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
  }, [teams, filters, sortField, sortDirection, teamProductsMap, getTrialTimeRemaining, getTeamRegions, getTeamTotalSpend, applyFilters]);

  const hasActiveFilters = filters.length > 0;

  // Pagination
  const {
    currentPage,
    pageSize,
    totalPages,
    totalItems,
    paginatedData,
    goToPage,
    changePageSize,
  } = useTablePagination(filteredAndSortedTeams, 10);

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

  if (isLoadingTeams || (teams.length > 0 && Object.keys(teamProductsMap).length === 0)) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <Loader2 className="h-8 w-8 animate-spin" />
        <div className="ml-3 text-sm text-muted-foreground">
          {isLoadingTeams ? 'Loading teams...' : 'Loading team products...'}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
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

      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead
                className="cursor-pointer hover:bg-gray-50"
                onClick={() => handleSort('admin_email')}
              >
                <div className="flex items-center gap-2">
                  <Users className="h-4 w-4" />
                  Team Email
                  {getSortIcon('admin_email')}
                </div>
              </TableHead>
              <TableHead
                className="cursor-pointer hover:bg-gray-50"
                onClick={() => handleSort('name')}
              >
                <div className="flex items-center gap-2">
                  Team Name
                  {getSortIcon('name')}
                </div>
              </TableHead>
              <TableHead
                className="cursor-pointer hover:bg-gray-50"
                onClick={() => handleSort('created_at')}
              >
                <div className="flex items-center gap-2">
                  <Calendar className="h-4 w-4" />
                  Team Create Date
                  {getSortIcon('created_at')}
                </div>
              </TableHead>
              <TableHead
                className="cursor-pointer hover:bg-gray-50"
                onClick={() => handleSort('last_payment')}
              >
                <div className="flex items-center gap-2">
                  <DollarSign className="h-4 w-4" />
                  Last Payment
                  {getSortIcon('last_payment')}
                </div>
              </TableHead>
              <TableHead
                className="cursor-pointer hover:bg-gray-50"
                onClick={() => handleSort('products')}
              >
                <div className="flex items-center gap-2">
                  <Package className="h-4 w-4" />
                  Products
                  {getSortIcon('products')}
                </div>
              </TableHead>
              <TableHead
                className="cursor-pointer hover:bg-gray-50"
                onClick={() => handleSort('trial_status')}
              >
                <div className="flex items-center gap-2">
                  <Calendar className="h-4 w-4" />
                  Trial Status
                  {getSortIcon('trial_status')}
                </div>
              </TableHead>
              <TableHead
                className="cursor-pointer hover:bg-gray-50"
                onClick={() => handleSort('regions')}
              >
                <div className="flex items-center gap-2">
                  <Globe className="h-4 w-4" />
                  Regions
                  {getSortIcon('regions')}
                </div>
              </TableHead>
              <TableHead
                className="cursor-pointer hover:bg-gray-50"
                onClick={() => handleSort('total_spend')}
              >
                <div className="flex items-center gap-2">
                  <DollarSign className="h-4 w-4" />
                  Total Spend
                  {getSortIcon('total_spend')}
                </div>
              </TableHead>
              <TableHead
                className="cursor-pointer hover:bg-gray-50"
                onClick={() => handleSort('hubspot_status')}
              >
                <div className="flex items-center gap-2">
                  <Users className="h-4 w-4" />
                  Hubspot Status
                  {getSortIcon('hubspot_status')}
                </div>
              </TableHead>

            </TableRow>
          </TableHeader>
          <TableBody>
            {paginatedData.length === 0 ? (
              <TableRow>
                <TableCell colSpan={10} className="text-center py-6">
                  No teams found matching your filters.
                </TableCell>
              </TableRow>
            ) : (
              paginatedData.map((team) => (
                <TableRow key={team.id} className="hover:bg-muted/50">
                  <TableCell className="font-medium">
                    {team.admin_email}
                  </TableCell>
                  <TableCell>
                    <div className="font-medium">{team.name}</div>
                    <div className="text-sm text-muted-foreground">
                      ID: {team.id}
                    </div>
                  </TableCell>
                  <TableCell>
                    {formatDate(team.created_at)}
                  </TableCell>
                  <TableCell>
                    {formatDate(team.last_payment)}
                  </TableCell>
                  <TableCell>
                    {(() => {
                      const teamProducts = teamProductsMap[team.id] || [];
                      return teamProducts.length > 0 ? (
                        <div className="space-y-1">
                          {teamProducts.map((product) => (
                            <Badge
                              key={product.id}
                              variant={product.active ? "default" : "secondary"}
                              className="mr-1"
                            >
                              {product.name}
                            </Badge>
                          ))}
                        </div>
                      ) : (
                        <span className="text-muted-foreground">No products</span>
                      );
                    })()}
                  </TableCell>
                  <TableCell>
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
                      } else if (trialStatus.includes('days remaining')) {
                        // Extract the number of days
                        const daysMatch = trialStatus.match(/(\d+)/);
                        if (daysMatch) {
                          const days = parseInt(daysMatch[1]);
                          // Calculate color: 30 days = green, 0 days = red
                          const ratio = Math.max(0, Math.min(1, days / 30));
                          const red = Math.round(255 * (1 - ratio));
                          const green = Math.round(255 * ratio);
                          customStyle = {
                            backgroundColor: `rgb(${red}, ${green}, 0)`,
                            color: 'white',
                            fontWeight: 'bold'
                          };
                        }
                      }

                      return (
                        <Badge
                          variant={badgeVariant}
                          style={customStyle}
                        >
                          {trialStatus}
                        </Badge>
                      );
                    })()}
                  </TableCell>
                  <TableCell>
                    {(() => {
                      const regions = getTeamRegions(team.id);
                      if (regions.length === 0) {
                        return <span className="text-muted-foreground">No regions</span>;
                      }
                      return (
                        <div className="space-y-1">
                          {regions.map((region) => (
                            <Badge
                              key={region}
                              variant="outline"
                              className="mr-1"
                            >
                              {region}
                            </Badge>
                          ))}
                        </div>
                      );
                    })()}
                  </TableCell>
                  <TableCell>
                    {(() => {
                      const totalSpend = getTeamTotalSpend(team.id);
                      const spendColor = getSpendColor(totalSpend);
                      return (
                        <div className="font-medium" style={{ color: spendColor }}>
                          {formatCurrency(totalSpend)}
                        </div>
                      );
                    })()}
                  </TableCell>
                                    <TableCell>
                    {(() => {
                      const hubspotStatus = localHubspotStatuses[team.id] || team.hubspot_status || 'Needs Import';

                      // TODO: This will use the "update-sales-data" API after it has been built
                      const handleStatusChange = (newStatus: string) => {
                        console.log(`Updating team ${team.id} hubspot status to: ${newStatus}`);
                        // Update local state immediately
                        setLocalHubspotStatuses(prev => ({
                          ...prev,
                          [team.id]: newStatus
                        }));
                        // TODO: Implement API call to update-sales-data endpoint
                      };

                      // Get color styling based on status
                      const getStatusColor = (status: string) => {
                        switch (status) {
                          case 'Import Successful':
                            return 'bg-green-600 text-white border-green-600';
                          case 'Import Failed':
                            return 'bg-red-600 text-white border-red-600';
                          case 'Skip Import':
                            return 'bg-gray-500 text-white border-gray-500';
                          default: // 'Needs Import'
                            return 'bg-gray-100 text-gray-700 border-gray-300';
                        }
                      };

                      return (
                        <Select
                          value={hubspotStatus}
                          onValueChange={handleStatusChange}
                        >
                          <SelectTrigger className={`w-40 ${getStatusColor(hubspotStatus)}`}>
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="Needs Import">Needs Import</SelectItem>
                            <SelectItem value="Import Successful">Import Successful</SelectItem>
                            <SelectItem value="Import Failed">Import Failed</SelectItem>
                            <SelectItem value="Skip Import">Skip Import</SelectItem>
                          </SelectContent>
                        </Select>
                      );
                    })()}
                  </TableCell>

                </TableRow>
              ))
            )}
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
