'use client';

import { useState, useMemo, Fragment } from 'react';
import { useQueryClient } from '@tanstack/react-query';
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
import { Loader2, ChevronDown, ChevronRight, ChevronUp, ChevronsUpDown } from 'lucide-react';
import { TableFilters, FilterField } from '@/components/ui/table-filters';
import { Switch } from '@/components/ui/switch';
import { Label } from '@/components/ui/label';

import { Team } from '@/types/team';
import { CreateTeamDialog } from './_components/create-team-dialog';
import { EditTeamDialog } from './_components/edit-team-dialog';
import { AddUserToTeamDialog } from './_components/add-user-to-team-dialog';
import { CreateUserInTeamDialog } from './_components/create-user-in-team-dialog';
import { SubscribeToProductDialog } from './_components/subscribe-to-product-dialog';
import { MergeTeamsDialog } from './_components/merge-teams-dialog';
import { TeamExpansionRow } from './_components/team-expansion-row';
import { useTeams } from '@/hooks/use-teams';

type SortField = 'name' | 'admin_email' | 'is_active' | 'created_at' | null;
type SortDirection = 'asc' | 'desc';

export default function TeamsPage() {
  const [expandedTeamId, setExpandedTeamId] = useState<string | null>(null);
  const [includeDeleted, setIncludeDeleted] = useState(false);

  // Dialog states
  const [isAddingTeam, setIsAddingTeam] = useState(false);
  const [isMergingTeams, setIsMergingTeams] = useState(false);
  const [isEditingTeam, setIsEditingTeam] = useState(false);
  const [isAddingUserToTeam, setIsAddingUserToTeam] = useState(false);
  const [isCreatingUserInTeam, setIsCreatingUserInTeam] = useState(false);
  const [isSubscribingToProduct, setIsSubscribingToProduct] = useState(false);

  const [selectedTeamId, setSelectedTeamId] = useState<string | null>(null);
  const [editingTeam, setEditingTeam] = useState<Team | null>(null);

  // Filter and sort state
  const [nameFilter, setNameFilter] = useState('');
  const [adminEmailFilter, setAdminEmailFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [sortField, setSortField] = useState<SortField>(null);
  const [sortDirection, setSortDirection] = useState<SortDirection>('asc');

  // Use centralized hook
  const { teams, isLoading: isLoadingTeams } = useTeams(includeDeleted);

  // Filtered and sorted teams
  const filteredAndSortedTeams = useMemo(() => {
    const filtered = teams.filter(team => {
      const nameMatch = team.name.toLowerCase().includes(nameFilter.toLowerCase());
      const adminEmailMatch = team.admin_email.toLowerCase().includes(adminEmailFilter.toLowerCase());
      const statusMatch = statusFilter === 'all' ||
        (statusFilter === 'active' && team.is_active) ||
        (statusFilter === 'inactive' && !team.is_active);

      return nameMatch && adminEmailMatch && statusMatch;
    });

    if (sortField) {
      filtered.sort((a, b) => {
        let aValue: string | boolean | Date;
        let bValue: string | boolean | Date;

        switch (sortField) {
          case 'name':
            aValue = a.name.toLowerCase();
            bValue = b.name.toLowerCase();
            break;
          case 'admin_email':
            aValue = a.admin_email.toLowerCase();
            bValue = b.admin_email.toLowerCase();
            break;
          case 'is_active':
            aValue = a.is_active;
            bValue = b.is_active;
            break;
          case 'created_at':
            aValue = new Date(a.created_at);
            bValue = new Date(b.created_at);
            break;
          default:
            return 0;
        }

        if (typeof aValue === 'string' && typeof bValue === 'string') {
          return sortDirection === 'asc' ? aValue.localeCompare(bValue) : bValue.localeCompare(aValue);
        } else if (typeof aValue === 'boolean' && typeof bValue === 'boolean') {
          return sortDirection === 'asc' ? (aValue === bValue ? 0 : aValue ? -1 : 1) : (aValue === bValue ? 0 : aValue ? 1 : -1);
        } else if (aValue instanceof Date && bValue instanceof Date) {
          return sortDirection === 'asc' ? aValue.getTime() - bValue.getTime() : bValue.getTime() - aValue.getTime();
        }
        return 0;
      });
    }
    return filtered;
  }, [teams, nameFilter, adminEmailFilter, statusFilter, sortField, sortDirection]);

  const {
    currentPage, pageSize, totalPages, totalItems, paginatedData, goToPage, changePageSize,
  } = useTablePagination(filteredAndSortedTeams, 10);

  const hasActiveFilters = Boolean(nameFilter.trim() || adminEmailFilter.trim() || statusFilter !== 'all');

  const filterFields: FilterField[] = [
    { key: 'name', label: 'Filter by Name', type: 'search', placeholder: 'Search by team name...', value: nameFilter, onChange: setNameFilter },
    { key: 'adminEmail', label: 'Filter by Admin Email', type: 'search', placeholder: 'Search by admin email...', value: adminEmailFilter, onChange: setAdminEmailFilter },
    {
      key: 'status', label: 'Filter by Status', type: 'select', placeholder: 'All statuses', value: statusFilter, onChange: setStatusFilter,
      options: [{ value: 'all', label: 'All statuses' }, { value: 'active', label: 'Active' }, { value: 'inactive', label: 'Inactive' }],
    },
  ];

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortDirection('asc');
    }
  };

  const getSortIcon = (field: SortField) => {
    if (sortField !== field) return <ChevronsUpDown className="h-4 w-4" />;
    return sortDirection === 'asc' ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />;
  };

  const isTeamExpired = (team: Team): boolean => {
    if (team.products && team.products.some(product => product.active)) return false;
    const thirtyDaysAgo = new Date();
    thirtyDaysAgo.setDate(thirtyDaysAgo.getDate() - 30);
    return !team.last_payment ? new Date(team.created_at) < thirtyDaysAgo : new Date(team.last_payment) < thirtyDaysAgo;
  };

  return (
    <div className="container mx-auto py-10">
      <div className="space-y-4">
        <div className="flex justify-between items-center">
          <h1 className="text-3xl font-bold">Teams</h1>
          <div className="flex space-x-2">
            <MergeTeamsDialog teams={teams} open={isMergingTeams} onOpenChange={setIsMergingTeams} />
            <CreateTeamDialog open={isAddingTeam} onOpenChange={setIsAddingTeam} />
          </div>
        </div>

        <TableFilters
          filters={filterFields}
          onClearFilters={() => { setNameFilter(''); setAdminEmailFilter(''); setStatusFilter('all'); setSortField(null); setSortDirection('asc'); }}
          hasActiveFilters={hasActiveFilters}
          totalItems={teams.length}
          filteredItems={filteredAndSortedTeams.length}
        />

        <div className="flex items-center space-x-2 py-2">
          <Switch id="include-deleted" checked={includeDeleted} onCheckedChange={setIncludeDeleted} />
          <Label htmlFor="include-deleted" className="cursor-pointer">Show deleted teams</Label>
        </div>

        {isLoadingTeams ? (
          <div className="flex justify-center items-center h-64"><Loader2 className="h-8 w-8 animate-spin" /></div>
        ) : (
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-10"></TableHead>
                  <TableHead className="cursor-pointer hover:bg-gray-50" onClick={() => handleSort('name')}>
                    <div className="flex items-center gap-2">Name{getSortIcon('name')}</div>
                  </TableHead>
                  <TableHead className="cursor-pointer hover:bg-gray-50" onClick={() => handleSort('admin_email')}>
                    <div className="flex items-center gap-2">Admin Email{getSortIcon('admin_email')}</div>
                  </TableHead>
                  <TableHead className="cursor-pointer hover:bg-gray-50" onClick={() => handleSort('is_active')}>
                    <div className="flex items-center gap-2">Status{getSortIcon('is_active')}</div>
                  </TableHead>
                  <TableHead className="cursor-pointer hover:bg-gray-50" onClick={() => handleSort('created_at')}>
                    <div className="flex items-center gap-2">Created At{getSortIcon('created_at')}</div>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {paginatedData.length === 0 ? (
                  <TableRow><TableCell colSpan={5} className="text-center py-6">No teams found. Create a new team to get started.</TableCell></TableRow>
                ) : (
                  paginatedData.map((team) => (
                    <Fragment key={team.id}>
                      <TableRow className={`cursor-pointer hover:bg-muted/50 ${team.deleted_at ? 'opacity-50' : ''}`} onClick={() => setExpandedTeamId(expandedTeamId === team.id ? null : team.id)}>
                        <TableCell>{expandedTeamId === team.id ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}</TableCell>
                        <TableCell className="font-medium">{team.name}</TableCell>
                        <TableCell>{team.admin_email}</TableCell>
                        <TableCell>
                          <div className="flex flex-col gap-1">
                            {team.deleted_at ? (
                              <span className="inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium bg-gray-800 text-white">DELETED</span>
                            ) : (
                              <><span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${team.is_active ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'}`}>{team.is_active ? 'Active' : 'Inactive'}</span>
                                {isTeamExpired(team) && <span className="inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium bg-red-600 text-white">Expired</span>}</>
                            )}
                          </div>
                        </TableCell>
                        <TableCell>{new Date(team.created_at).toLocaleDateString()}</TableCell>
                      </TableRow>
                      <TeamExpansionRow 
                        teamId={team.id} 
                        isExpanded={expandedTeamId === team.id} 
                        includeDeleted={includeDeleted}
                        onEdit={(t) => { setEditingTeam(t); setIsEditingTeam(true); }}
                        onAddUser={(id) => { setSelectedTeamId(id); setIsAddingUserToTeam(true); }}
                        onCreateUser={(id) => { setSelectedTeamId(id); setIsCreatingUserInTeam(true); }}
                        onSubscribe={(id) => { setSelectedTeamId(id); setIsSubscribingToProduct(true); }}
                      />
                    </Fragment>
                  ))
                )}
              </TableBody>
            </Table>
          </div>
        )}

        <TablePagination currentPage={currentPage} totalPages={totalPages} pageSize={pageSize} totalItems={totalItems} onPageChange={goToPage} onPageSizeChange={changePageSize} />

        <EditTeamDialog team={editingTeam} open={isEditingTeam} onOpenChange={setIsEditingTeam} />
        <AddUserToTeamDialog teamId={selectedTeamId} open={isAddingUserToTeam} onOpenChange={setIsAddingUserToTeam} />
        <CreateUserInTeamDialog teamId={selectedTeamId} open={isCreatingUserInTeam} onOpenChange={setIsCreatingUserInTeam} />
        <SubscribeToProductDialog teamId={selectedTeamId} open={isSubscribingToProduct} onOpenChange={setIsSubscribingToProduct} />
      </div>
    </div>
  );
}
