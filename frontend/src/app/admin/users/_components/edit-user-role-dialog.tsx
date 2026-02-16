import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { post } from '@/utils/api';
import { useToast } from '@/hooks/use-toast';
import { USER_ROLES } from '@/types/user';

interface EditUserRoleDialogProps {
  user: { id: string; currentRole: string } | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function EditUserRoleDialog({ user, open, onOpenChange }: EditUserRoleDialogProps) {
  const { toast } = useToast();
  const queryClient = useQueryClient();

  const updateUserRoleMutation = useMutation({
    mutationFn: async ({ userId, role }: { userId: string; role: string }) => {
      const response = await post(`/users/${userId}/role`, { role });
      return response.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
      onOpenChange(false);
      toast({
        title: 'Success',
        description: 'User role updated successfully',
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

  const handleConfirmRoleUpdate = (newRole: string) => {
    if (!user) return;
    updateUserRoleMutation.mutate({ userId: user.id, role: newRole });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Update User Role</DialogTitle>
          <DialogDescription>
            Select a new role for this user. This will change their permissions within the system.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4 py-4">
          <div className="space-y-2">
            <label className="text-sm font-medium">Role</label>
            <Select
              value={user?.currentRole || 'read_only'}
              onValueChange={handleConfirmRoleUpdate}
            >
              <SelectTrigger>
                <SelectValue placeholder="Select a role" />
              </SelectTrigger>
              <SelectContent>
                {USER_ROLES.map((role) => (
                  <SelectItem key={role.value} value={role.value}>
                    {role.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
