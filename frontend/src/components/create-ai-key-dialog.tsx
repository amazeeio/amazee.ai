"use client";

import { Loader2, Plus, ChevronsUpDown, Check } from "lucide-react";
import * as React from "react";
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
import { cn } from "@/lib/utils";
import { Region } from "@/types/region";
import { User } from "@/types/user";
import { Team } from "@/types/team";

interface CreateAIKeyDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (data: {
    name: string;
    region_id: number;
    key_type: "full" | "llm" | "vector";
    owner_id?: number;
    team_id?: number;
  }) => void;
  isLoading?: boolean;
  regions: Region[];
  teamMembers?: User[];
  teams?: Team[];
  showUserAssignment?: boolean;
  currentUser?:
    | User
    | {
        id: number;
        email: string;
        team_id?: number | null;
      };
  triggerText?: string;
  title?: string;
  description?: string;
  children?: React.ReactNode;
}

export function CreateAIKeyDialog({
  open,
  onOpenChange,
  onSubmit,
  isLoading = false,
  regions,
  teamMembers = [],
  teams = [],
  showUserAssignment = false,
  currentUser,
  triggerText = "Create AI Key",
  title = "Create New AI Key",
  description = "Create a new AI key with database credentials.",
  children,
}: CreateAIKeyDialogProps) {
  const [name, setName] = React.useState("");
  const [selectedRegion, setSelectedRegion] = React.useState("");
  const [keyType, setKeyType] = React.useState<"full" | "llm" | "vector">(
    "full",
  );
  const [selectedUserId, setSelectedUserId] = React.useState(() => {
    // Default to current user if available, otherwise "team" or empty
    if (currentUser?.id) return currentUser.id.toString();
    if (currentUser?.team_id) return "team";
    return "";
  });
  const [userSearchOpen, setUserSearchOpen] = React.useState(false);
  const [userSearchTerm, setUserSearchTerm] = React.useState("");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();

    if (!name || !selectedRegion) return;

    const region = regions.find((r) => r.name === selectedRegion);
    if (!region) return;

    const data: {
      name: string;
      region_id: number;
      key_type: "full" | "llm" | "vector";
      owner_id?: number;
      team_id?: number;
    } = {
      name,
      region_id: region.id,
      key_type: keyType,
    };

    if (showUserAssignment) {
      if (selectedUserId === "team") {
        // Current user's team shared
        if (currentUser?.team_id) {
          data.team_id = currentUser.team_id;
        }
      } else if (selectedUserId.startsWith("team-")) {
        // Specific team from global list
        data.team_id = parseInt(selectedUserId.replace("team-", ""));
      } else if (
        selectedUserId === currentUser?.id.toString() ||
        selectedUserId === "self"
      ) {
        if (currentUser?.id) {
          data.owner_id = Number(currentUser.id);
        }
      } else if (selectedUserId.startsWith("user-")) {
        // Specific user from global list
        data.owner_id = parseInt(selectedUserId.replace("user-", ""));
      } else {
        // Compatibility with old behavior where selectedUserId was just the ID
        const id = parseInt(selectedUserId);
        if (!isNaN(id)) {
          data.owner_id = id;
        }
      }
    }

    onSubmit(data);
  };

  const handleOpenChange = (newOpen: boolean) => {
    if (!newOpen) {
      // Reset form when closing
      setName("");
      setSelectedRegion("");
      setKeyType("full");
      setSelectedUserId(currentUser?.id.toString() || (currentUser?.team_id ? "team" : ""));
      setUserSearchTerm("");
    }
    onOpenChange(newOpen);
  };

  // Filter team members and teams based on search term
  const filteredTeamMembers = React.useMemo(() => {
    if (!userSearchTerm) return teamMembers;
    const searchLower = userSearchTerm.toLowerCase();
    return teamMembers.filter((member) =>
      member.email.toLowerCase().includes(searchLower),
    );
  }, [teamMembers, userSearchTerm]);

  const filteredTeams = React.useMemo(() => {
    if (!userSearchTerm) return teams;
    const searchLower = userSearchTerm.toLowerCase();
    return teams.filter((team) => team.name.toLowerCase().includes(searchLower));
  }, [teams, userSearchTerm]);

  // Get display text for selected user/team
  const getSelectedUserDisplay = () => {
    if (selectedUserId === "team") return "My Team (Shared)";
    if (selectedUserId.startsWith("team-")) {
      const teamId = selectedUserId.replace("team-", "");
      const team = teams.find((t) => t.id.toString() === teamId);
      return team ? `Team: ${team.name}` : "Selected Team";
    }
    if (
      selectedUserId === currentUser?.id.toString() ||
      selectedUserId === "self"
    ) {
      return currentUser?.email || "Me";
    }
    
    // Check if it's user-prefix
    const userId = selectedUserId.startsWith("user-") 
      ? selectedUserId.replace("user-", "")
      : selectedUserId;
      
    const selectedMember = teamMembers.find(
      (m) => m.id.toString() === userId,
    );
    return selectedMember?.email || "Select user or team...";
  };

  const isFormValid =
    name && selectedRegion && (!showUserAssignment || selectedUserId);

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        {children || (
          <Button>
            <Plus className="mr-2 h-4 w-4" />
            {triggerText}
          </Button>
        )}
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit}>
          <div className="grid gap-4 py-4">
            <div className="grid gap-2">
              <label htmlFor="name" className="text-sm font-medium">
                Name <span className="text-red-500">*</span>
              </label>
              <Input
                id="name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="My AI Key"
                required
              />
              <p className="text-sm text-muted-foreground">
                A descriptive name to help you identify this key
              </p>
            </div>

            <div className="grid gap-2">
              <label htmlFor="type" className="text-sm font-medium">
                Type <span className="text-red-500">*</span>
              </label>
              <Select
                value={keyType}
                onValueChange={(value: "full" | "llm" | "vector") =>
                  setKeyType(value)
                }
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select a type" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="full">
                    Full Key (LLM + Vector DB)
                  </SelectItem>
                  <SelectItem value="llm">LLM Token Only</SelectItem>
                  <SelectItem value="vector">Vector DB Only</SelectItem>
                </SelectContent>
              </Select>
              <p className="text-sm text-muted-foreground">
                Choose whether to create a full key with both LLM and Vector DB
                access, or just one component
              </p>
            </div>

            <div className="grid gap-2">
              <label htmlFor="region" className="text-sm font-medium">
                Region <span className="text-red-500">*</span>
              </label>
              <Select value={selectedRegion} onValueChange={setSelectedRegion}>
                <SelectTrigger>
                  <SelectValue placeholder="Select a region" />
                </SelectTrigger>
                <SelectContent>
                  {regions
                    .filter((region) => region.is_active)
                    .map((region) => (
                      <SelectItem key={region.id} value={region.name}>
                        {region.name}
                      </SelectItem>
                    ))}
                </SelectContent>
              </Select>
            </div>

            {showUserAssignment && (
              <div className="grid gap-2">
                <label htmlFor="user" className="text-sm font-medium">
                  Assign to <span className="text-red-500">*</span>
                </label>
                <Popover open={userSearchOpen} onOpenChange={setUserSearchOpen}>
                  <PopoverTrigger asChild>
                    <Button
                      variant="outline"
                      role="combobox"
                      aria-controls="user-search-popover"
                      aria-expanded={userSearchOpen}
                      className="w-full justify-between"
                    >
                      {getSelectedUserDisplay()}
                      <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
                    </Button>
                  </PopoverTrigger>
                  <PopoverContent id="user-search-popover" className="w-full p-0" align="start">
                    <Command>
                      <CommandInput
                        placeholder="Search users..."
                        value={userSearchTerm}
                        onValueChange={setUserSearchTerm}
                      />
                      <CommandList>
                        <CommandEmpty>No results found.</CommandEmpty>
                        
                        {teams.length > 0 && (
                          <CommandGroup heading="Teams">
                            {filteredTeams.map((team) => (
                              <CommandItem
                                key={`team-${team.id}`}
                                value={`team-${team.name}`}
                                onSelect={() => {
                                  setSelectedUserId(`team-${team.id}`);
                                  setUserSearchOpen(false);
                                  setUserSearchTerm("");
                                }}
                              >
                                <Check
                                  className={cn(
                                    "mr-2 h-4 w-4",
                                    selectedUserId === `team-${team.id}`
                                      ? "opacity-100"
                                      : "opacity-0",
                                  )}
                                />
                                {team.name}
                              </CommandItem>
                            ))}
                          </CommandGroup>
                        )}

                        <CommandGroup heading="Users">
                          {currentUser?.team_id && (
                            <CommandItem
                              value="team"
                              onSelect={() => {
                                setSelectedUserId("team");
                                setUserSearchOpen(false);
                                setUserSearchTerm("");
                              }}
                            >
                              <Check
                                className={cn(
                                  "mr-2 h-4 w-4",
                                  selectedUserId === "team"
                                    ? "opacity-100"
                                    : "opacity-0",
                                )}
                              />
                              My Team (Shared)
                            </CommandItem>
                          )}
                          {currentUser && (
                            <CommandItem
                              value={currentUser.email}
                              onSelect={() => {
                                setSelectedUserId(currentUser.id.toString());
                                setUserSearchOpen(false);
                                setUserSearchTerm("");
                              }}
                            >
                              <Check
                                className={cn(
                                  "mr-2 h-4 w-4",
                                  selectedUserId === currentUser.id.toString()
                                    ? "opacity-100"
                                    : "opacity-0",
                                )}
                              />
                              {currentUser.email} (Me)
                            </CommandItem>
                          )}
                          {filteredTeamMembers
                            .filter((member) => member.id !== currentUser?.id)
                            .map((member) => (
                              <CommandItem
                                key={member.id}
                                value={`user-${member.email}`}
                                onSelect={() => {
                                  setSelectedUserId(`user-${member.id}`);
                                  setUserSearchOpen(false);
                                  setUserSearchTerm("");
                                }}
                              >
                                <Check
                                  className={cn(
                                    "mr-2 h-4 w-4",
                                    selectedUserId === `user-${member.id}` || selectedUserId === member.id.toString()
                                      ? "opacity-100"
                                      : "opacity-0",
                                  )}
                                />
                                {member.email}
                              </CommandItem>
                            ))}
                        </CommandGroup>
                      </CommandList>
                    </Command>
                  </PopoverContent>
                </Popover>
                <p className="text-sm text-muted-foreground">
                  {teams.length > 0
                    ? "Select a team or a specific user to assign this key to"
                    : teamMembers.length > 0
                      ? "Select 'My Team (Shared)' to create a key accessible to all team members, or assign to a specific user"
                      : currentUser?.team_id
                        ? "Select 'My Team (Shared)' to create a key accessible to all team members, or assign to yourself"
                        : "Assign this key to yourself"}
                </p>
              </div>
            )}
          </div>
          <DialogFooter>
            <Button type="submit" disabled={isLoading || !isFormValid}>
              {isLoading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Create Key
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
