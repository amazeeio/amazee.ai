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
import { Loader2, Plus } from 'lucide-react';
import { useToast } from '@/hooks/use-toast';
import { put, post } from '@/utils/api';
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

// Enum constants matching the backend
const LIMIT_TYPES = [
  { value: 'control_plane', label: 'Control Plane' },
  { value: 'data_plane', label: 'Data Plane' },
] as const;

const RESOURCE_TYPES = [
  // Control Plane Resources
  { value: 'user_key', label: 'User Keys', limitType: 'control_plane' },
  { value: 'service_key', label: 'Service Keys', limitType: 'control_plane' },
  { value: 'user', label: 'Users', limitType: 'control_plane' },
  { value: 'vector_db', label: 'Vector DBs', limitType: 'control_plane' },
  { value: 'gpt_instance', label: 'GPT Instances', limitType: 'control_plane' },
  // Data Plane Resources
  { value: 'max_budget', label: 'Max Budget', limitType: 'data_plane' },
  { value: 'rpm', label: 'RPM', limitType: 'data_plane' },
  { value: 'storage', label: 'Storage', limitType: 'data_plane' },
  { value: 'document', label: 'Documents', limitType: 'data_plane' },
] as const;

const UNIT_TYPES = [
  { value: 'count', label: 'Count' },
  { value: 'dollar', label: 'Dollar ($)' },
  { value: 'gigabyte', label: 'Gigabyte (GB)' },
] as const;

interface LimitsViewProps {
  limits: LimitedResource[];
  isLoading: boolean;
  ownerType: 'team' | 'user' | 'system';
  ownerId: string;
  queryKey: string[];
  showResetAll?: boolean;
  onResetAll?: () => void;
  isResettingAll?: boolean;
  allowIndividualReset?: boolean;
}

export function LimitsView({
  limits,
  isLoading,
  ownerType,
  ownerId,
  queryKey,
  showResetAll = false,
  onResetAll,
  isResettingAll = false,
  allowIndividualReset = true,
}: LimitsViewProps) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [isEditingLimit, setIsEditingLimit] = useState(false);
  const [editingLimitId, setEditingLimitId] = useState<number | null>(null);
  const [editMaxValue, setEditMaxValue] = useState<number>(0);

  // Create limit dialog state
  const [isCreatingLimit, setIsCreatingLimit] = useState(false);
  const [newLimitType, setNewLimitType] = useState<string>('');
  const [newResourceType, setNewResourceType] = useState<string>('');
  const [newMaxValue, setNewMaxValue] = useState<number>(0);
  const [newCurrentValue, setNewCurrentValue] = useState<number | null>(null);

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

  const createLimitMutation = useMutation({
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
          throw new Error(`Failed to create limit: ${error.message}`);
        } else {
          throw new Error('An unexpected error occurred while creating the limit.');
        }
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey });
      toast({
        title: 'Success',
        description: 'Limit created successfully',
      });
      setIsCreatingLimit(false);
      // Reset form
      setNewLimitType('');
      setNewResourceType('');
      setNewMaxValue(0);
      setNewCurrentValue(null);
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

  const handleCreateLimit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newLimitType || !newResourceType) {
      toast({
        title: 'Error',
        description: 'Please fill in all required fields',
        variant: 'destructive',
      });
      return;
    }

    // For control plane limits, current_value is required
    if (newLimitType === 'control_plane' && (newCurrentValue === null || newCurrentValue === undefined)) {
      toast({
        title: 'Error',
        description: 'Current value is required for control plane limits',
        variant: 'destructive',
      });
      return;
    }

    const unit = getUnitForResource(newResourceType);

    createLimitMutation.mutate({
      owner_type: ownerType,
      owner_id: parseInt(ownerId),
      resource_type: newResourceType,
      limit_type: newLimitType,
      unit: unit,
      max_value: newMaxValue,
      current_value: newCurrentValue,
    });
  };

  // Get filtered resource types based on selected limit type
  const getFilteredResourceTypes = () => {
    if (!newLimitType) return RESOURCE_TYPES;
    return RESOURCE_TYPES.filter(resource => resource.limitType === newLimitType);
  };

  // Get the unit for a given resource type
  const getUnitForResource = (resourceType: string): string => {
    const unitMapping: Record<string, string> = {
      'user_key': 'count',
      'service_key': 'count',
      'user': 'count',
      'vector_db': 'count',
      'gpt_instance': 'count',
      'max_budget': 'dollar',
      'rpm': 'count',
      'storage': 'gigabyte',
      'document': 'count',
    };
    return unitMapping[resourceType] || 'count';
  };

  // Get the current unit based on selected resource type
  const currentUnit = newResourceType ? getUnitForResource(newResourceType) : '';

  const formatResourceName = (resource: string): string => {
    const mapping: Record<string, string> = {
      'user_key': 'User Keys',
      'service_key': 'Service Keys',
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
      <div className="flex justify-between items-center">
        <h3 className="text-lg font-medium">Resource Limits</h3>
        <div className="flex gap-2">
          <Dialog open={isCreatingLimit} onOpenChange={setIsCreatingLimit}>
            <DialogTrigger asChild>
              <Button size="sm">
                <Plus className="mr-2 h-4 w-4" />
                Create New Limit
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Create New Limit</DialogTitle>
                <DialogDescription>
                  Create a new limit for this {ownerType}. This will override any existing product or default limits.
                </DialogDescription>
              </DialogHeader>
              <form onSubmit={handleCreateLimit} className="space-y-4">
                <div className="space-y-2">
                  <label className="text-sm font-medium">Limit Type</label>
                  <Select
                    value={newLimitType}
                    onValueChange={(value) => {
                      setNewLimitType(value);
                      setNewResourceType(''); // Reset resource type when limit type changes
                      // Reset current value when limit type changes
                      setNewCurrentValue(null);
                    }}
                    required
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="Select limit type" />
                    </SelectTrigger>
                    <SelectContent>
                      {LIMIT_TYPES.map((type) => (
                        <SelectItem key={type.value} value={type.value}>
                          {type.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="space-y-2">
                  <label className="text-sm font-medium">Resource Type</label>
                  <Select
                    value={newResourceType}
                    onValueChange={setNewResourceType}
                    required
                    disabled={!newLimitType}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="Select resource type" />
                    </SelectTrigger>
                    <SelectContent>
                      {getFilteredResourceTypes().map((resource) => (
                        <SelectItem key={resource.value} value={resource.value}>
                          {resource.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                {newResourceType && (
                  <div className="space-y-2">
                    <label className="text-sm font-medium">Unit</label>
                    <div className="px-3 py-2 border rounded-md bg-muted text-sm">
                      {UNIT_TYPES.find(unit => unit.value === currentUnit)?.label || currentUnit}
                    </div>
                    <p className="text-xs text-muted-foreground">
                      Unit is automatically determined by the resource type
                    </p>
                  </div>
                )}

                <div className="space-y-2">
                  <label className="text-sm font-medium">Max Value</label>
                  <Input
                    type="number"
                    value={newMaxValue}
                    onChange={(e) => {
                      const value = e.target.value;
                      if (currentUnit === 'count') {
                        setNewMaxValue(parseInt(value) || 0);
                      } else {
                        setNewMaxValue(parseFloat(value) || 0);
                      }
                    }}
                    placeholder="Enter max value"
                    required
                    min="0"
                    step={currentUnit === 'count' ? '1' : '0.01'}
                  />
                </div>

                <div className="space-y-2">
                  <label className="text-sm font-medium">
                    Current Value {newLimitType === 'control_plane' ? '(Required)' : '(Optional)'}
                  </label>
                  <Input
                    type="number"
                    value={newCurrentValue !== null ? newCurrentValue : ''}
                    onChange={(e) => {
                      const value = e.target.value;
                      if (value === '') {
                        setNewCurrentValue(null);
                      } else {
                        if (currentUnit === 'count') {
                          const numValue = parseInt(value);
                          setNewCurrentValue(isNaN(numValue) ? null : numValue);
                        } else {
                          const numValue = parseFloat(value);
                          setNewCurrentValue(isNaN(numValue) ? null : numValue);
                        }
                      }
                    }}
                    placeholder={newLimitType === 'control_plane' ? 'Enter current value' : 'Enter current value (optional)'}
                    min="0"
                    step={currentUnit === 'count' ? '1' : '0.01'}
                    required={newLimitType === 'control_plane'}
                  />
                  {newLimitType === 'control_plane' && (
                    <p className="text-xs text-muted-foreground">
                      Current value is required for control plane limits
                    </p>
                  )}
                </div>

                <DialogFooter>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => setIsCreatingLimit(false)}
                  >
                    Cancel
                  </Button>
                  <Button
                    type="submit"
                    disabled={createLimitMutation.isPending}
                  >
                    {createLimitMutation.isPending ? (
                      <>
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        Creating...
                      </>
                    ) : (
                      'Create Limit'
                    )}
                  </Button>
                </DialogFooter>
              </form>
            </DialogContent>
          </Dialog>

          {showResetAll && onResetAll && (
            <DeleteConfirmationDialog
              title={`Reset All ${ownerType === 'team' ? 'Team' : 'User'} Limits`}
              description={`Are you sure you want to reset all limits for this ${ownerType}? This will revert all manual overrides back to product or default limits.`}
              triggerText={`Reset All ${ownerType === 'team' ? 'Team' : 'User'} Limits`}
              confirmText="Reset"
              onConfirm={onResetAll}
              isLoading={isResettingAll}
              size="sm"
            />
          )}
        </div>
      </div>
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
                        onChange={(e) => {
                          const value = e.target.value;
                          if (limit.unit === 'count') {
                            setEditMaxValue(parseInt(value) || 0);
                          } else {
                            setEditMaxValue(parseFloat(value) || 0);
                          }
                        }}
                        className="w-24"
                        required
                        step={limit.unit === 'count' ? '1' : '0.01'}
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
                        {allowIndividualReset && (
                          <DeleteConfirmationDialog
                            title="Reset Limit"
                            description="Are you sure you want to reset this limit? This will revert any manual override back to the product or default limit."
                            triggerText="Reset"
                            confirmText="Reset"
                            onConfirm={() => handleResetLimit(limit)}
                            isLoading={resetLimitMutation.isPending}
                            size="sm"
                          />
                        )}
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
