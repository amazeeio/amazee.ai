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
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Eye, EyeOff, Pencil, Loader2 } from 'lucide-react';
import { formatTimeUntil } from '@/lib/utils';
import { PrivateAIKey } from '@/types/private-ai-key';

interface PrivateAIKeysTableProps {
  keys: PrivateAIKey[];
  onDelete: (keyId: number) => void;
  isLoading?: boolean;
  showOwner?: boolean;
  showSpend?: boolean;
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
  showSpend = false,
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

  const togglePasswordVisibility = (keyId: number | string) => {
    setShowPassword(prev => ({
      ...prev,
      [keyId]: !prev[keyId]
    }));
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
      </div>
    );
  }

  return (
    <div className="rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Name</TableHead>
            <TableHead>Database Credentials</TableHead>
            <TableHead>LLM Credentials</TableHead>
            <TableHead>Region</TableHead>
            {showOwner && <TableHead>Owner</TableHead>}
            {showSpend && <TableHead>Spend</TableHead>}
            <TableHead>Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {keys.map((key, index) => (
            <TableRow key={key.id || `key-${index}`}>
              <TableCell>{key.name}</TableCell>
              <TableCell>
                <div className="space-y-2">
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
              {showSpend && (
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
                        {onUpdateBudget && (
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
                  ) : (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => onLoadSpend?.(key.id)}
                    >
                      Load Spend
                    </Button>
                  )}
                </TableCell>
              )}
              <TableCell>
                <AlertDialog>
                  <AlertDialogTrigger asChild>
                    <Button variant="destructive" size="sm">Delete</Button>
                  </AlertDialogTrigger>
                  <AlertDialogContent>
                    <AlertDialogHeader>
                      <AlertDialogTitle>Delete Private AI Key</AlertDialogTitle>
                      <AlertDialogDescription>
                        Are you sure you want to delete this private AI key? This action cannot be undone.
                      </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                      <AlertDialogCancel>Cancel</AlertDialogCancel>
                      <AlertDialogAction
                        onClick={() => onDelete(key.id)}
                        className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                      >
                        {isDeleting ? (
                          <>
                            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                            Deleting...
                          </>
                        ) : (
                          'Delete'
                        )}
                      </AlertDialogAction>
                    </AlertDialogFooter>
                  </AlertDialogContent>
                </AlertDialog>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}