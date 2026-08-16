import { Loader2, Plus } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useTeams } from "@/hooks/use-teams";
import { Region } from "@/types/region";

interface CreateTeamDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  regions: Region[];
}

export function CreateTeamDialog({
  open,
  onOpenChange,
  regions,
}: CreateTeamDialogProps) {
  const [newTeamName, setNewTeamName] = useState("");
  const [newTeamAdminEmail, setNewTeamAdminEmail] = useState("");
  const [newTeamRegionId, setNewTeamRegionId] = useState("");
  const { createTeam, isCreating } = useTeams(false, { enabled: false });

  // A team is locked to a single region on creation, and the backend rejects
  // inactive or dedicated regions — only offer the ones it will accept.
  const selectableRegions = regions.filter(
    (r) => r.is_active && !r.is_dedicated,
  );

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const regionId = Number(newTeamRegionId);
    if (!Number.isFinite(regionId) || regionId <= 0) return;
    createTeam(
      {
        name: newTeamName,
        admin_email: newTeamAdminEmail,
        region_id: regionId,
      },
      {
        onSuccess: () => {
          onOpenChange(false);
          setNewTeamName("");
          setNewTeamAdminEmail("");
          setNewTeamRegionId("");
        },
      },
    );
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
            <label htmlFor="new-team-name" className="text-sm font-medium">
              Name
            </label>
            <Input
              id="new-team-name"
              value={newTeamName}
              onChange={(e) => setNewTeamName(e.target.value)}
              placeholder="Team Name"
              required
            />
          </div>
          <div className="space-y-2">
            <label
              htmlFor="new-team-admin-email"
              className="text-sm font-medium"
            >
              Admin Email
            </label>
            <Input
              id="new-team-admin-email"
              type="email"
              value={newTeamAdminEmail}
              onChange={(e) => setNewTeamAdminEmail(e.target.value)}
              placeholder="admin@example.com"
              required
            />
          </div>
          <div className="space-y-2">
            <label htmlFor="new-team-region" className="text-sm font-medium">
              Region
            </label>
            <Select
              value={newTeamRegionId}
              onValueChange={setNewTeamRegionId}
              required
            >
              <SelectTrigger id="new-team-region">
                <SelectValue placeholder="Select a region" />
              </SelectTrigger>
              <SelectContent>
                {selectableRegions.map((region) => (
                  <SelectItem key={region.id} value={region.id.toString()}>
                    {region.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              The team is permanently created in this region. Dedicated regions
              must be assigned afterwards from the Regions page.
            </p>
          </div>
          <DialogFooter>
            <Button
              type="submit"
              disabled={isCreating || newTeamRegionId === ""}
            >
              {isCreating ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Creating...
                </>
              ) : (
                "Create Team"
              )}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
