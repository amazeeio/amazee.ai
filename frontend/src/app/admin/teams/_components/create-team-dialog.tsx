import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { Loader2, Plus } from 'lucide-react';
import { useTeams } from '@/hooks/use-teams';

interface CreateTeamDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function CreateTeamDialog({ open, onOpenChange }: CreateTeamDialogProps) {
  const [newTeamName, setNewTeamName] = useState('');
  const [newTeamAdminEmail, setNewTeamAdminEmail] = useState('');
  const { createTeam, isCreating } = useTeams();

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    createTeam({
      name: newTeamName,
      admin_email: newTeamAdminEmail,
    }, {
      onSuccess: () => {
        onOpenChange(false);
        setNewTeamName('');
        setNewTeamAdminEmail('');
      }
    });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogTrigger asChild>
        <Button>
          <Plus className="mr-2 h-4 w-4" />
          Add Team
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add New Team</DialogTitle>
          <DialogDescription>
            Create a new team for private AI hosting.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <label className="text-sm font-medium">Name</label>
            <Input
              value={newTeamName}
              onChange={(e) => setNewTeamName(e.target.value)}
              placeholder="Team Name"
              required
            />
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium">Admin Email</label>
            <Input
              type="email"
              value={newTeamAdminEmail}
              onChange={(e) => setNewTeamAdminEmail(e.target.value)}
              placeholder="admin@example.com"
              required
            />
          </div>
          <DialogFooter>
            <Button
              type="submit"
              disabled={isCreating}
            >
              {isCreating ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Creating...
                </>
              ) : (
                'Create Team'
              )}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
