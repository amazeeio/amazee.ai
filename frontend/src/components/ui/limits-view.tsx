'use client';

import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { Loader2 } from 'lucide-react';
import { useToast } from '@/hooks/use-toast';
import { put, post } from '@/utils/api';
import { DeleteConfirmationDialog } from '@/components/ui/delete-confirmation-dialog';

export interface LimitedResource {
  id: number;
  limit_type: 'control_plane' | 'data_plane';
  resource: string;
  unit: 'count' | 'dollar' | 'gigabyte';
  max_value: number;
  current_value: number | null;
  owner_type: 'system' | 'team' | 'user';
  owner_id: number;
  limited_by: 'product' | 'default' | 'manual';
  set_by: string | null;
  created_at: string;
  updated_at: string | null;
}

interface LimitsViewProps {
  limits: LimitedResource[];
  isLoading: boolean;
  ownerType: 'team' | 'user' | 'system';
  ownerId: string;
  queryKey: string[];
  showResetAll?: boolean;
  onResetAll?: () => void;
  isResettingAll?: boolean;
}

export function LimitsView({
  limits,
  isLoading,
  ownerType,
  ownerId: _ownerId, // eslint-disable-line @typescript-eslint/no-unused-vars
  queryKey,
  showResetAll = false,
  onResetAll,
  isResettingAll = false,
}: LimitsViewProps) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [isEditingLimit, setIsEditingLimit] = useState(false);
  const [editingLimitId, setEditingLimitId] = useState<number | null>(null);
  const [editMaxValue, setEditMaxValue] = useState<number>(0);

  const updateLimitMutation = useMutation({
    mutationFn: async (limitData: {
      owner_type: string;
      owner_id: number;
      resource_type: string;
      limit_type: string;
      unit: string;
      max_value: number;
      current_value: number | null;
    }) => {
      try {
        const response = await put('/limits/overwrite', limitData);
        return response.json();
      } catch (error) {
        if (error instanceof Error) {
          throw new Error(`Failed to update limit: ${error.message}`);
        } else {
          throw new Error('An unexpected error occurred while updating the limit.');
        }
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey });
      toast({
        title: 'Success',
        description: 'Limit updated successfully',
      });
      setIsEditingLimit(false);
      setEditingLimitId(null);
    },
    onError: (error: Error) => {
      toast({
        title: 'Error',
        description: error.message,
        variant: 'destructive',
      });
    },
  });

  const resetLimitMutation = useMutation({
    mutationFn: async (limitData: {
      owner_type: string;
      owner_id: number;
      resource_type: string;
    }) => {
      try {
        const response = await post('/limits/reset', limitData);
        return response.json();
      } catch (error) {
        if (error instanceof Error) {
          throw new Error(`Failed to reset limit: ${error.message}`);
        } else {
          throw new Error('An unexpected error occurred while resetting the limit.');
        }
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey });
      toast({
        title: 'Success',
        description: 'Limit reset successfully',
      });
    },
    onError: (error: Error) => {
      toast({
        title: 'Error',
        description: error.message,
        variant: 'destructive',
      });
    },
  });

  const handleEditLimit = (limit: LimitedResource) => {
    setEditingLimitId(limit.id);
    setEditMaxValue(limit.max_value);
    setIsEditingLimit(true);
  };

  const handleSaveLimit = (limit: LimitedResource) => {
    updateLimitMutation.mutate({
      owner_type: limit.owner_type,
      owner_id: limit.owner_id,
      resource_type: limit.resource,
      limit_type: limit.limit_type,
      unit: limit.unit,
      max_value: editMaxValue,
      current_value: limit.current_value,
    });
  };

  const handleResetLimit = (limit: LimitedResource) => {
    resetLimitMutation.mutate({
      owner_type: limit.owner_type,
      owner_id: limit.owner_id,
      resource_type: limit.resource,
    });
  };

  const formatResourceName = (resource: string): string => {
    const mapping: Record<string, string> = {
      'ai_key': 'AI Keys',
      'user': 'Users',
      'vector_db': 'Vector DBs',
      'gpt_instance': 'GPT Instances',
      'max_budget': 'Max Budget',
      'rpm': 'RPM',
      'storage': 'Storage',
      'document': 'Documents',
    };
    return mapping[resource] || resource;
  };

  const formatUnit = (unit: string): string => {
    const mapping: Record<string, string> = {
      'count': '',
      'dollar': '$',
      'gigabyte': 'GB',
    };
    return mapping[unit] || unit;
  };

  const formatValue = (value: number | null, unit: string): string => {
    if (value === null) return 'N/A';

    const unitSymbol = formatUnit(unit);
    if (unit === 'count') {
      return value.toString();
    }
    return `${unitSymbol}${value}`;
  };

  const formatLimitType = (limitType: string): string => {
    const mapping: Record<string, string> = {
      'control_plane': 'Control Plane',
      'data_plane': 'Data Plane',
    };
    return mapping[limitType] || limitType;
  };

  const formatLimitSource = (source: string): string => {
    const mapping: Record<string, string> = {
      'product': 'Product',
      'default': 'Default',
      'manual': 'Manual',
    };
    return mapping[source] || source;
  };

  const formatOwnerType = (ownerType: string): string => {
    const mapping: Record<string, string> = {
      'system': 'System',
      'team': 'Team',
      'user': 'User',
    };
    return mapping[ownerType] || ownerType;
  };

  if (isLoading) {
    return (
      <div className="flex justify-center items-center py-8">
        <Loader2 className="h-8 w-8 animate-spin" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {showResetAll && onResetAll && (
        <div className="flex justify-between items-center">
          <h3 className="text-lg font-medium">Resource Limits</h3>
          <DeleteConfirmationDialog
            title={`Reset All ${ownerType === 'team' ? 'Team' : 'User'} Limits`}
            description={`Are you sure you want to reset all limits for this ${ownerType}? This will revert all manual overrides back to product or default limits.`}
            triggerText={`Reset All ${ownerType === 'team' ? 'Team' : 'User'} Limits`}
            confirmText="Reset"
            onConfirm={onResetAll}
            isLoading={isResettingAll}
            size="sm"
          />
        </div>
      )}
      {limits.length > 0 ? (
        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Resource</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Owner Type</TableHead>
                <TableHead>Current Value</TableHead>
                <TableHead>Max Value</TableHead>
                <TableHead>Source</TableHead>
                <TableHead>Set By</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {limits.map((limit) => (
                <TableRow key={limit.id}>
                  <TableCell className="font-medium">
                    {formatResourceName(limit.resource)}
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline">
                      {formatLimitType(limit.limit_type)}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <Badge
                      variant={
                        limit.owner_type === 'user' ? 'default' :
                        limit.owner_type === 'team' ? 'secondary' :
                        'outline'
                      }
                    >
                      {formatOwnerType(limit.owner_type)}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <span>
                      {formatValue(limit.current_value, limit.unit)}
                    </span>
                  </TableCell>
                  <TableCell>
                    {isEditingLimit && editingLimitId === limit.id ? (
                      <Input
                        type="number"
                        value={editMaxValue}
                        onChange={(e) => setEditMaxValue(parseFloat(e.target.value))}
                        className="w-24"
                        required
                      />
                    ) : (
                      <span>
                        {formatValue(limit.max_value, limit.unit)}
                      </span>
                    )}
                  </TableCell>
                  <TableCell>
                    <Badge
                      variant={
                        limit.limited_by === 'manual' ? 'default' :
                        limit.limited_by === 'product' ? 'secondary' :
                        'outline'
                      }
                    >
                      {formatLimitSource(limit.limited_by)}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    {limit.set_by ? (
                      <span className="text-sm">{limit.set_by}</span>
                    ) : (
                      <span className="text-muted-foreground text-sm">—</span>
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    {isEditingLimit && editingLimitId === limit.id ? (
                      <div className="flex justify-end gap-2">
                        <Button
                          size="sm"
                          onClick={() => handleSaveLimit(limit)}
                          disabled={updateLimitMutation.isPending}
                        >
                          {updateLimitMutation.isPending ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : (
                            'Save'
                          )}
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => {
                            setIsEditingLimit(false);
                            setEditingLimitId(null);
                          }}
                        >
                          Cancel
                        </Button>
                      </div>
                    ) : (
                      <div className="flex justify-end gap-2">
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => handleEditLimit(limit)}
                        >
                          Edit
                        </Button>
                        <DeleteConfirmationDialog
                          title="Reset Limit"
                          description="Are you sure you want to reset this limit? This will revert any manual override back to the product or default limit."
                          triggerText="Reset"
                          confirmText="Reset"
                          onConfirm={() => handleResetLimit(limit)}
                          isLoading={resetLimitMutation.isPending}
                          size="sm"
                        />
                      </div>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      ) : (
        <div className="text-center py-8 border rounded-md">
          <p className="text-muted-foreground">No limits configured for this {ownerType}.</p>
        </div>
      )}
    </div>
  );
}
