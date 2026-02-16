import { useState, useEffect } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
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
import { Loader2 } from 'lucide-react';
import { post } from '@/utils/api';
import { useToast } from '@/hooks/use-toast';
import { USER_ROLES } from '@/types/user';
import { getCachedConfig } from '@/utils/config';

interface CreateUserInTeamDialogProps {
  teamId: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function CreateUserInTeamDialog({ teamId, open, onOpenChange }: CreateUserInTeamDialogProps) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [newUserEmail, setNewUserEmail] = useState('');
  const [newUserPassword, setNewUserPassword] = useState('');
  const [newUserRole, setNewUserRole] = useState('read_only');
  const [isPasswordless, setIsPasswordless] = useState(false);

  useEffect(() => {
    const config = getCachedConfig();
    setIsPasswordless(config.PASSWORDLESS_SIGN_IN);
  }, []);

  const createUserMutation = useMutation({
    mutationFn: async (userData: { email: string; password?: string; team_id?: number; role: string }) => {
      const response = await post('/users', userData);
      return response.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['team', teamId] });
      queryClient.invalidateQueries({ queryKey: ['teams'] });
      onOpenChange(false);
      setNewUserEmail('');
      setNewUserPassword('');
      setNewUserRole('read_only');
      toast({
        title: 'Success',
        description: 'User created successfully',
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

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!teamId) return;

    const userData: {
      email: string;
      password?: string;
      team_id?: number;
      role: string;
    } = {
      email: newUserEmail,
      role: newUserRole,
      team_id: parseInt(teamId),
    };

    if (!isPasswordless) {
      userData.password = newUserPassword;
    }

    createUserMutation.mutate(userData);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Create New User</DialogTitle>
          <DialogDescription>
            Create a new user and add them to this team.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit}>
          <div className="grid gap-4 py-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">Email</label>
              <Input
                type="email"
                value={newUserEmail}
                onChange={(e) => setNewUserEmail(e.target.value)}
                placeholder="user@example.com"
                required
              />
            </div>
            {!isPasswordless && (
              <div className="space-y-2">
                <label className="text-sm font-medium">Password</label>
                <Input
                  type="password"
                  value={newUserPassword}
                  onChange={(e) => setNewUserPassword(e.target.value)}
                  placeholder="••••••••"
                  required
                />
              </div>
            )}
            <div className="space-y-2">
              <label className="text-sm font-medium">Role</label>
              <Select
                value={newUserRole}
                onValueChange={setNewUserRole}
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
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={createUserMutation.isPending}
            >
              {createUserMutation.isPending && (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              )}
              Create User
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
