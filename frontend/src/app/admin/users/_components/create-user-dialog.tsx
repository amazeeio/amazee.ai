import { Loader2, ChevronsUpDown, Check } from "lucide-react";
import { useState, useEffect, useMemo } from "react";
import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
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
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useToast } from "@/hooks/use-toast";
import { cn } from "@/lib/utils";
import { USER_ROLES } from "@/types/user";
import { post } from "@/utils/api";
import { useMutation, useQueryClient } from "@tanstack/react-query";

interface Team {
  id: string;
  name: string;
}

interface CreateUserDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  teams: Team[];
}

export function CreateUserDialog({
  open,
  onOpenChange,
  teams,
}: CreateUserDialogProps) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [newUserEmail, setNewUserEmail] = useState("");
  const [newUserPassword, setNewUserPassword] = useState("");
  const [newUserRole, setNewUserRole] = useState("read_only");
  const [newUserTeamId, setNewUserTeamId] = useState<string>("");
  const [isSystemUser, setIsSystemUser] = useState(false);
  const [teamSearchOpen, setTeamSearchOpen] = useState(false);
  const [teamSearchTerm, setTeamSearchTerm] = useState("");

  // Filter teams based on search term
  const filteredTeams = useMemo(() => {
    if (!teamSearchTerm) return teams;
    const searchLower = teamSearchTerm.toLowerCase();
    return teams.filter((team) =>
      team.name.toLowerCase().includes(searchLower),
    );
  }, [teams, teamSearchTerm]);

  // Get display text for selected team
  const getSelectedTeamDisplay = () => {
    if (!newUserTeamId) return "Select a team...";
    const selectedTeam = teams.find((t) => String(t.id) === newUserTeamId);
    return selectedTeam?.name || "Select a team...";
  };

  // Update role when switching between system and team user types
  useEffect(() => {
    if (isSystemUser) {
      setNewUserRole("admin"); // Default to admin for system users
    } else {
      setNewUserRole("read_only"); // Default to read_only for team users
    }
  }, [isSystemUser]);

  const createUserMutation = useMutation({
    mutationFn: async (userData: {
      email: string;
      password?: string;
      role?: string;
      team_id?: string;
      is_system_user?: boolean;
    }) => {
      const response = await post("/users", userData);
      return response.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["users"] });
      onOpenChange(false);
      setNewUserEmail("");
      setNewUserPassword("");
      setNewUserRole("read_only");
      setNewUserTeamId("");
      setIsSystemUser(false);
      setTeamSearchTerm("");
      toast({
        title: "Success",
        description: "User created successfully",
      });
    },
    onError: (error: Error) => {
      toast({
        title: "Error",
        description: error.message,
        variant: "destructive",
      });
    },
  });

  const handleCreateUser = (e: React.FormEvent) => {
    e.preventDefault();
    const userData: {
      email: string;
      password?: string;
      role?: string;
      team_id?: string;
      is_system_user?: boolean;
    } = {
      email: newUserEmail,
      is_system_user: isSystemUser,
    };

    if (newUserPassword.trim()) {
      userData.password = newUserPassword;
    }

    if (!isSystemUser) {
      userData.role = newUserRole;
      if (newUserTeamId) {
        userData.team_id = newUserTeamId;
      }
    } else {
      userData.role = newUserRole;
    }

    createUserMutation.mutate(userData);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogTrigger asChild>
        <Button>Add User</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add New User</DialogTitle>
          <DialogDescription>
            Create a new user account. The user will be able to log in with
            these credentials.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleCreateUser} className="space-y-4">
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
          <div className="space-y-2">
            <label className="text-sm font-medium">Password</label>
            <Input
              type="password"
              value={newUserPassword}
              onChange={(e) => setNewUserPassword(e.target.value)}
              placeholder="••••••••"
            />
            <p className="text-xs text-muted-foreground">
              Leave empty to allow passwordless sign-in (if enabled)
            </p>
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium">User Type</label>
            <div className="flex items-center space-x-4">
              <label className="flex items-center space-x-2">
                <input
                  type="radio"
                  checked={!isSystemUser}
                  onChange={() => setIsSystemUser(false)}
                  className="form-radio"
                />
                <span>Team User</span>
              </label>
              <label className="flex items-center space-x-2">
                <input
                  type="radio"
                  checked={isSystemUser}
                  onChange={() => setIsSystemUser(true)}
                  className="form-radio"
                />
                <span>System User</span>
              </label>
            </div>
          </div>
          {!isSystemUser && (
            <>
              <div className="space-y-2">
                <label className="text-sm font-medium">Team</label>
                <Popover open={teamSearchOpen} onOpenChange={setTeamSearchOpen}>
                  <PopoverTrigger asChild>
                    <Button
                      variant="outline"
                      role="combobox"
                      aria-controls="team-search-popover"
                      aria-expanded={teamSearchOpen}
                      className={cn(
                        "w-full justify-between font-normal",
                        !newUserTeamId && "text-muted-foreground"
                      )}
                    >
                      {getSelectedTeamDisplay()}
                      <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
                    </Button>
                  </PopoverTrigger>
                  <PopoverContent id="team-search-popover" className="w-full p-0" align="start">
                    <Command>
                      <CommandInput
                        placeholder="Search teams..."
                        value={teamSearchTerm}
                        onValueChange={setTeamSearchTerm}
                      />
                      <CommandList>
                        <CommandEmpty>No teams found.</CommandEmpty>
                        <CommandGroup>
                          {filteredTeams.map((team) => (
                            <CommandItem
                              key={team.id}
                              value={team.name}
                              onSelect={() => {
                                setNewUserTeamId(String(team.id));
                                setTeamSearchOpen(false);
                                setTeamSearchTerm("");
                              }}
                            >
                              <Check
                                className={cn(
                                  "mr-2 h-4 w-4",
                                  newUserTeamId === String(team.id)
                                    ? "opacity-100"
                                    : "opacity-0",
                                )}
                              />
                              {team.name}
                            </CommandItem>
                          ))}
                        </CommandGroup>
                      </CommandList>
                    </Command>
                  </PopoverContent>
                </Popover>
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium">Team Role</label>
                <Select value={newUserRole} onValueChange={setNewUserRole}>
                  <SelectTrigger>
                    <SelectValue placeholder="Select a role" />
                  </SelectTrigger>
                  <SelectContent>
                    {USER_ROLES.filter((role) => role.value !== "sales").map(
                      (role) => (
                        <SelectItem key={role.value} value={role.value}>
                          {role.label}
                        </SelectItem>
                      ),
                    )}
                  </SelectContent>
                </Select>
              </div>
            </>
          )}
          {isSystemUser && (
            <div className="space-y-2">
              <label className="text-sm font-medium">System Role</label>
              <Select value={newUserRole} onValueChange={setNewUserRole}>
                <SelectTrigger>
                  <SelectValue placeholder="Select a system role" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="admin">Admin</SelectItem>
                  <SelectItem value="sales">Sales</SelectItem>
                </SelectContent>
              </Select>
            </div>
          )}
          <DialogFooter>
            <Button type="submit" disabled={createUserMutation.isPending || (!isSystemUser && !newUserTeamId)}>
              {createUserMutation.isPending ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Creating...
                </>
              ) : (
                "Create User"
              )}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
