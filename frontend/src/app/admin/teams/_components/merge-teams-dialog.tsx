import { Loader2 } from "lucide-react";
import { useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Combobox } from "@/components/ui/combobox";
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
import { Team } from "@/types/team";

interface MergeTeamsDialogProps {
  teams: Team[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function MergeTeamsDialog({
  teams,
  open,
  onOpenChange,
}: MergeTeamsDialogProps) {
  const [targetTeamId, setTargetTeamId] = useState("");
  const [sourceTeamId, setSourceTeamId] = useState("");
  const [conflictResolutionStrategy, setConflictResolutionStrategy] = useState<
    "delete" | "rename" | "cancel"
  >("rename");
  const [renameSuffix, setRenameSuffix] = useState("_merged");
  const { mergeTeams, isMerging } = useTeams();

  const handleMerge = (e: React.FormEvent) => {
    e.preventDefault();
    if (!targetTeamId || !sourceTeamId) return;

    mergeTeams(
      {
        targetTeamId,
        sourceTeamId,
        conflictResolutionStrategy,
        renameSuffix:
          conflictResolutionStrategy === "rename" ? renameSuffix : undefined,
      },
      {
        onSuccess: () => {
          onOpenChange(false);
          setTargetTeamId("");
          setSourceTeamId("");
        },
      },
    );
  };

  const teamOptions = teams.map((team) => ({
    value: team.id.toString(),
    label: `${team.name} (${team.admin_email})`,
  }));

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogTrigger asChild>
        <Button variant="outline">Merge Teams</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Merge Teams</DialogTitle>
          <DialogDescription>
            Merge another team into the target team. Users and keys from the
            source team will be moved to the target team, and the source team
            will be deleted. This action cannot be undone.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleMerge} className="space-y-4">
          <div className="space-y-2">
            <label className="text-sm font-medium">Target Team</label>
            <Combobox
              options={teamOptions}
              value={targetTeamId}
              onValueChange={setTargetTeamId}
              placeholder="Select target team"
              searchPlaceholder="Search teams..."
            />
          </div>
          <Alert>
            <AlertDescription>
              <strong>Warning:</strong> This operation will permanently delete
              the source team after merging its users and keys into the target
              team. Make sure you have selected the correct teams.
            </AlertDescription>
          </Alert>
          <div className="space-y-2">
            <label className="text-sm font-medium">Source Team</label>
            <Combobox
              options={teamOptions.filter((o) => o.value !== targetTeamId)}
              value={sourceTeamId}
              onValueChange={setSourceTeamId}
              placeholder="Select a team to merge from"
              searchPlaceholder="Search teams..."
            />
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium">Conflict Resolution</label>
            <Select
              value={conflictResolutionStrategy}
              onValueChange={(value: "delete" | "rename" | "cancel") =>
                setConflictResolutionStrategy(value)
              }
            >
              <SelectTrigger>
                <SelectValue placeholder="Select a strategy" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="delete">
                  Delete conflicting keys from source team
                </SelectItem>
                <SelectItem value="rename">
                  Rename conflicting keys from source team
                </SelectItem>
                <SelectItem value="cancel">
                  Cancel merge if conflicts exist
                </SelectItem>
              </SelectContent>
            </Select>
          </div>
          {conflictResolutionStrategy === "rename" && (
            <div className="space-y-2">
              <label className="text-sm font-medium">Rename Suffix</label>
              <Input
                value={renameSuffix}
                onChange={(e) => setRenameSuffix(e.target.value)}
                placeholder="e.g., _merged"
              />
            </div>
          )}
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
              disabled={isMerging || !targetTeamId || !sourceTeamId}
            >
              {isMerging && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Merge Teams
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
