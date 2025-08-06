import { useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { DeleteConfirmationDialog } from '@/components/ui/delete-confirmation-dialog';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Eye, EyeOff, Pencil, Loader2, ArrowUp, ArrowDown, ArrowUpDown } from 'lucide-react';
import { formatTimeUntil } from '@/lib/utils';
import { PrivateAIKey } from '@/types/private-ai-key';

type SortField = 'name' | 'region' | 'owner' | null;
type SortDirection = 'asc' | 'desc';
type KeyType = 'full' | 'llm' | 'vector' | 'all';

interface PrivateAIKeysTableProps {
  keys: PrivateAIKey[];
  onDelete: (keyId: number) => void;
  isLoading?: boolean;
  showOwner?: boolean;
  allowModification?: boolean;
  spendMap?: Record<number, {
    spend: number;
    max_budget: number | null;
    budget_duration: string | null;
    budget_reset_at: string | null;
  }>;
  onLoadSpend?: (keyId: number) => void;
  onUpdateBudget?: (keyId: number, budgetDuration: string) => void;
  isDeleting?: boolean;
  isUpdatingBudget?: boolean;
  teamDetails?: Record<number, { name: string }>;
  teamMembers?: { id: number; email: string }[];
}

export function PrivateAIKeysTable({
  keys,
  onDelete,
  isLoading = false,
  showOwner = false,
  allowModification = false,
  spendMap = {},
  onLoadSpend,
  onUpdateBudget,
  isDeleting = false,
  isUpdatingBudget = false,
  teamDetails = {},
  teamMembers = [],
}: PrivateAIKeysTableProps) {
  const [showPassword, setShowPassword] = useState<Record<number | string, boolean>>({});
  const [openBudgetDialog, setOpenBudgetDialog] = useState<number | null>(null);
  const [sortField, setSortField] = useState<SortField>(null);
  const [sortDirection, setSortDirection] = useState<SortDirection>('asc');
  const [keyTypeFilter, setKeyTypeFilter] = useState<KeyType>('all');

  const togglePasswordVisibility = (keyId: number | string) => {
    setShowPassword(prev => ({
      ...prev,
      [keyId]: !prev[keyId]
    }));
  };

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortDirection('asc');
    }
  };

  const getSortedAndFilteredKeys = () => {
    let filteredKeys = keys;

    // Apply key type filter
    if (keyTypeFilter !== 'all') {
      filteredKeys = filteredKeys.filter(key => {
        if (keyTypeFilter === 'full') {
          return key.litellm_token && key.database_name;
        } else if (keyTypeFilter === 'llm') {
          return key.litellm_token && !key.database_name;
        } else if (keyTypeFilter === 'vector') {
          return !key.litellm_token && key.database_name;
        }
        return true;
      });
    }

    // Apply sorting
    if (sortField) {
      filteredKeys.sort((a, b) => {
        let aValue: string | number = '';
        let bValue: string | number = '';

        if (sortField === 'name') {
          aValue = a.name || '';
          bValue = b.name || '';
        } else if (sortField === 'region') {
          aValue = a.region || '';
          bValue = b.region || '';
        } else if (sortField === 'owner') {
          if (a.owner_id) {
            const owner = teamMembers.find(member => member.id === a.owner_id);
            aValue = owner?.email || `User ${a.owner_id}`;
          } else if (a.team_id) {
            aValue = `(Team) ${teamDetails[a.team_id]?.name || 'Team (Shared)'}`;
          }
          if (b.owner_id) {
            const owner = teamMembers.find(member => member.id === b.owner_id);
            bValue = owner?.email || `User ${b.owner_id}`;
          } else if (b.team_id) {
            bValue = `(Team) ${teamDetails[b.team_id]?.name || 'Team (Shared)'}`;
          }
        }

        if (sortDirection === 'asc') {
          return aValue > bValue ? 1 : -1;
        } else {
          return aValue < bValue ? 1 : -1;
        }
      });
    }

    return filteredKeys;
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4">
        <Select value={keyTypeFilter} onValueChange={(value: KeyType) => setKeyTypeFilter(value)}>
          <SelectTrigger className="w-[180px]">
            <SelectValue placeholder="Filter by type" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Keys</SelectItem>
            <SelectItem value="full">Full Keys</SelectItem>
            <SelectItem value="llm">LLM Only</SelectItem>
            <SelectItem value="vector">Vector DB Only</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>
                <Button
                  variant="ghost"
                  onClick={() => handleSort('name')}
                  className="flex items-center gap-1"
                >
                  Name
                  {sortField === 'name' ? (
                    sortDirection === 'asc' ? (
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
                  onClick={() => handleSort('region')}
                  className="flex items-center gap-1"
                >
                  Region
                  {sortField === 'region' ? (
                    sortDirection === 'asc' ? (
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
                    onClick={() => handleSort('owner')}
                    className="flex items-center gap-1"
                  >
                    Owner
                    {sortField === 'owner' ? (
                      sortDirection === 'asc' ? (
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
            {getSortedAndFilteredKeys().map((key, index) => (
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
                            {showPassword[key.id || `key-${index}`] ? key.database_password : '••••••••'}
                          </span>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => togglePasswordVisibility(key.id || `key-${index}`)}
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
                      <span className="text-muted-foreground">No Vector DB</span>
                    )}
                  </div>
                </TableCell>
                <TableCell>
                  {key.litellm_token ? (
                    <div className="space-y-2">
                      <div className="flex items-center gap-2">
                        <span>Token: </span>
                        <span className="font-mono">
                          {showPassword[`${key.id}-token`] ? key.litellm_token : '••••••••'}
                        </span>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => togglePasswordVisibility(`${key.id}-token`)}
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
                    <span className="text-muted-foreground">No LLM credentials</span>
                  )}
                </TableCell>
                <TableCell>{key.region}</TableCell>
                {showOwner && (
                  <TableCell>
                    <div className="flex flex-col gap-1">
                      {key.owner_id ? (
                        <span className="text-sm">
                          {teamMembers.find(member => member.id === key.owner_id)?.email || `User ${key.owner_id}`}
                        </span>
                      ) : key.team_id ? (
                        <span className="text-sm">(Team) {teamDetails[key.team_id]?.name || 'Team (Shared)'}</span>
                      ) : null}
                    </div>
                  </TableCell>
                )}
                <TableCell>
                  {spendMap[key.id] ? (
                    <div className="flex flex-col gap-1">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium">
                          ${spendMap[key.id].spend.toFixed(2)}
                        </span>
                        <span className="text-xs text-muted-foreground">
                          {spendMap[key.id]?.max_budget !== null
                            ? `/ $${spendMap[key.id]?.max_budget?.toFixed(2)}`
                            : '(No budget)'}
                        </span>
                      </div>
                      <span className="text-xs text-muted-foreground">
                        {spendMap[key.id].budget_duration || 'No budget period'}
                        {spendMap[key.id].budget_reset_at && ` • Resets ${formatTimeUntil(spendMap[key.id].budget_reset_at as string)}`}
                        {allowModification && onUpdateBudget && (
                          <Dialog open={openBudgetDialog === key.id} onOpenChange={(open) => setOpenBudgetDialog(open ? key.id : null)}>
                            <DialogTrigger asChild>
                              <Button variant="ghost" size="icon" className="h-4 w-4 ml-1">
                                <Pencil className="h-3 w-3" />
                              </Button>
                            </DialogTrigger>
                            <DialogContent>
                              <DialogHeader>
                                <DialogTitle>Update Budget Period</DialogTitle>
                                <DialogDescription>
                                  Set the budget period for this key. Examples: &quot;30d&quot; (30 days), &quot;24h&quot; (24 hours), &quot;60m&quot; (60 minutes)
                                </DialogDescription>
                              </DialogHeader>
                              <div className="grid gap-4 py-4">
                                <div className="grid gap-2">
                                  <Label htmlFor="budget-duration">Budget Period</Label>
                                  <Input
                                    id="budget-duration"
                                    defaultValue={spendMap[key.id].budget_duration || ''}
                                    placeholder="e.g. 30d"
                                  />
                                </div>
                              </div>
                              <DialogFooter>
                                <Button
                                  onClick={() => {
                                    const input = document.getElementById('budget-duration') as HTMLInputElement;
                                    if (input) {
                                      onUpdateBudget(key.id, input.value);
                                    }
                                  }}
                                  disabled={isUpdatingBudget}
                                >
                                  {isUpdatingBudget ? (
                                    <>
                                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                      Updating...
                                    </>
                                  ) : (
                                    'Update'
                                  )}
                                </Button>
                              </DialogFooter>
                            </DialogContent>
                          </Dialog>
                        )}
                      </span>
                    </div>
                  ) : key.litellm_token ? (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => onLoadSpend?.(key.id)}
                    >
                      Load Spend
                    </Button>
                  ) : null}
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
    </div>
  );
}