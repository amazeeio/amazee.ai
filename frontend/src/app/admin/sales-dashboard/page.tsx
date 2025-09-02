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

import { Loader2, ChevronUp, ChevronDown, ChevronsUpDown, DollarSign, Calendar, Users, Globe, Package } from 'lucide-react';
import { get } from '@/utils/api';
import { TableFilters, FilterField } from '@/components/ui/table-filters';
import { Badge } from '@/components/ui/badge';

interface Team {
  id: string;
  name: string;
  admin_email: string;
  created_at: string;
  last_payment?: string;
  is_always_free: boolean;
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


type SortField = 'admin_email' | 'name' | 'created_at' | 'last_payment' | 'product_count' | 'trial_status' | 'regions' | 'total_spend' | null;
type SortDirection = 'asc' | 'desc';

export default function SalesDashboardPage() {

  // Filter and sort state
  const [emailFilter, setEmailFilter] = useState('');
  const [nameFilter, setNameFilter] = useState('');
  const [productFilter, setProductFilter] = useState('all');
  const [trialStatusFilter, setTrialStatusFilter] = useState('all');
  const [regionsFilter, setRegionsFilter] = useState('all');
  const [sortField, setSortField] = useState<SortField>(null);
  const [sortDirection, setSortDirection] = useState<SortDirection>('asc');

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

  // Fetch available regions
  const { data: availableRegions = [] } = useQuery<Array<{ id: number; name: string }>>({
    queryKey: ['regions'],
    queryFn: async () => {
      const response = await get('/regions');
      const data = await response.json();
      return data;
    },
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

  // Filtered and sorted teams
  const filteredAndSortedTeams = useMemo(() => {
    const filtered = teams.filter(team => {
      const emailMatch = team.admin_email.toLowerCase().includes(emailFilter.toLowerCase());
      const nameMatch = team.name.toLowerCase().includes(nameFilter.toLowerCase());

      // Product filter
      const teamProducts = teamProductsMap[team.id] || [];
      let productMatch = true;
      if (productFilter === 'has_products') {
        productMatch = teamProducts.length > 0;
      } else if (productFilter === 'no_products') {
        productMatch = teamProducts.length === 0;
      } else if (productFilter === 'has_active_products') {
        productMatch = teamProducts.some(p => p.active);
      }

      // Trial status filter
      const trialStatus = getTrialTimeRemaining(team);
      let trialStatusMatch = true;
      if (trialStatusFilter === 'active_product') {
        trialStatusMatch = trialStatus === 'Active Product';
      } else if (trialStatusFilter === 'always_free') {
        trialStatusMatch = trialStatus === 'Always Free';
      } else if (trialStatusFilter === 'trial_active') {
        trialStatusMatch = trialStatus.includes('days remaining');
      } else if (trialStatusFilter === 'expired') {
        trialStatusMatch = trialStatus === 'Expired';
      }

      // Regions filter
      const teamRegions = getTeamRegions(team.id);
      let regionsMatch = true;
      if (regionsFilter === 'has_regions') {
        regionsMatch = teamRegions.length > 0;
      } else if (regionsFilter === 'no_regions') {
        regionsMatch = teamRegions.length === 0;
      } else if (regionsFilter !== 'all') {
        // Filter by specific region
        regionsMatch = teamRegions.includes(regionsFilter);
      }

      return emailMatch && nameMatch && productMatch && trialStatusMatch && regionsMatch;
    });

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
          case 'product_count':
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
  }, [teams, emailFilter, nameFilter, productFilter, trialStatusFilter, regionsFilter, sortField, sortDirection, teamProductsMap, getTrialTimeRemaining, getTeamRegions, getTeamTotalSpend]);

  const hasActiveFilters = Boolean(emailFilter.trim() || nameFilter.trim() || productFilter !== 'all' || trialStatusFilter !== 'all' || regionsFilter !== 'all');

  // Filter fields configuration
  const filterFields: FilterField[] = [
    {
      key: 'email',
      label: 'Filter by Team Email',
      type: 'search',
      placeholder: 'Search by team email...',
      value: emailFilter,
      onChange: setEmailFilter,
    },
    {
      key: 'name',
      label: 'Filter by Team Name',
      type: 'search',
      placeholder: 'Search by team name...',
      value: nameFilter,
      onChange: setNameFilter,
    },
    {
      key: 'products',
      label: 'Filter by Products',
      type: 'select',
      placeholder: 'All products',
      value: productFilter,
      onChange: setProductFilter,
      options: [
        { value: 'all', label: 'All products' },
        { value: 'has_products', label: 'Has products' },
        { value: 'no_products', label: 'No products' },
        { value: 'has_active_products', label: 'Has active products' },
      ],
    },
    {
      key: 'trial_status',
      label: 'Filter by Trial Status',
      type: 'select',
      placeholder: 'All statuses',
      value: trialStatusFilter,
      onChange: setTrialStatusFilter,
      options: [
        { value: 'all', label: 'All statuses' },
        { value: 'active_product', label: 'Active Product' },
        { value: 'always_free', label: 'Always Free' },
        { value: 'trial_active', label: 'Trial Active' },
        { value: 'expired', label: 'Expired' },
      ],
    },
    {
      key: 'regions',
      label: 'Filter by Regions',
      type: 'select',
      placeholder: 'All regions',
      value: regionsFilter,
      onChange: setRegionsFilter,
      options: [
        { value: 'all', label: 'All regions' },
        { value: 'has_regions', label: 'Has regions' },
        { value: 'no_regions', label: 'No regions' },
        ...availableRegions.map((region: { id: number; name: string }) => ({
          value: region.name,
          label: region.name
        }))
      ],
    },
  ];

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

      <TableFilters
        filters={filterFields}
        onClearFilters={() => {
          setEmailFilter('');
          setNameFilter('');
          setProductFilter('all');
          setTrialStatusFilter('all');
          setRegionsFilter('all');
          setSortField(null);
          setSortDirection('asc');
        }}
        hasActiveFilters={hasActiveFilters}
        totalItems={teams.length}
        filteredItems={filteredAndSortedTeams.length}
      />

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
                onClick={() => handleSort('product_count')}
              >
                <div className="flex items-center gap-2">
                  <Package className="h-4 w-4" />
                  Product Associations
                  {getSortIcon('product_count')}
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
