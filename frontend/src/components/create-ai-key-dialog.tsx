"use client"

import * as React from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { Loader2, Plus, ChevronsUpDown, Check } from "lucide-react"
import { cn } from "@/lib/utils"

interface Region {
  id: number
  name: string
  is_active: boolean
}

interface TeamUser {
  id: number
  email: string
  is_active: boolean
  role: string
}

interface CreateAIKeyDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSubmit: (data: {
    name: string
    region_id: number
    key_type: 'full' | 'llm' | 'vector'
    owner_id?: number
    team_id?: number
  }) => void
  isLoading?: boolean
  regions: Region[]
  teamMembers?: TeamUser[]
  showUserAssignment?: boolean
  currentUser?: {
    id: number
    email: string
    team_id?: number | null
  }
  triggerText?: string
  title?: string
  description?: string
  children?: React.ReactNode
}

export function CreateAIKeyDialog({
  open,
  onOpenChange,
  onSubmit,
  isLoading = false,
  regions,
  teamMembers = [],
  showUserAssignment = false,
  currentUser,
  triggerText = "Create AI Key",
  title = "Create New AI Key",
  description = "Create a new AI key with database credentials.",
  children,
}: CreateAIKeyDialogProps) {
  const [name, setName] = React.useState("")
  const [selectedRegion, setSelectedRegion] = React.useState("")
  const [keyType, setKeyType] = React.useState<'full' | 'llm' | 'vector'>('full')
  const [selectedUserId, setSelectedUserId] = React.useState(() => {
    // Default to current user if available, otherwise "team"
    return currentUser?.id.toString() || "team"
  })
  const [userSearchOpen, setUserSearchOpen] = React.useState(false)
  const [userSearchTerm, setUserSearchTerm] = React.useState("")

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()

    if (!name || !selectedRegion) return

    const region = regions.find(r => r.name === selectedRegion)
    if (!region) return

    const data: {
      name: string
      region_id: number
      key_type: 'full' | 'llm' | 'vector'
      owner_id?: number
      team_id?: number
    } = {
      name,
      region_id: region.id,
      key_type: keyType,
    }

    if (showUserAssignment) {
      if (selectedUserId === "team") {
        // Team shared - set team_id and no specific owner_id
        if (currentUser?.team_id) {
          data.team_id = currentUser.team_id
        }
      } else if (selectedUserId === currentUser?.id.toString() || selectedUserId === "self") {
        data.owner_id = currentUser?.id
      } else {
        data.owner_id = parseInt(selectedUserId)
      }
    }

    onSubmit(data)
  }

  const handleOpenChange = (newOpen: boolean) => {
    if (!newOpen) {
      // Reset form when closing
      setName("")
      setSelectedRegion("")
      setKeyType('full')
      setSelectedUserId(currentUser?.id.toString() || "team")
      setUserSearchTerm("")
    }
    onOpenChange(newOpen)
  }

  // Filter team members based on search term
  const filteredTeamMembers = React.useMemo(() => {
    if (!userSearchTerm) return teamMembers
    const searchLower = userSearchTerm.toLowerCase()
    return teamMembers.filter(member =>
      member.email.toLowerCase().includes(searchLower)
    )
  }, [teamMembers, userSearchTerm])

  // Get display text for selected user
  const getSelectedUserDisplay = () => {
    if (selectedUserId === "team") return "Team (Shared)"
    if (selectedUserId === currentUser?.id.toString() || selectedUserId === "self") {
      return currentUser?.email || "Me"
    }
    const selectedMember = teamMembers.find(m => m.id.toString() === selectedUserId)
    return selectedMember?.email || "Select user..."
  }

  const isFormValid = name && selectedRegion && (!showUserAssignment || selectedUserId)

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
          <DialogDescription>
            {description}
          </DialogDescription>
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
              <Select value={keyType} onValueChange={(value: 'full' | 'llm' | 'vector') => setKeyType(value)}>
                <SelectTrigger>
                  <SelectValue placeholder="Select a type" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="full">Full Key (LLM + Vector DB)</SelectItem>
                  <SelectItem value="llm">LLM Token Only</SelectItem>
                  <SelectItem value="vector">Vector DB Only</SelectItem>
                </SelectContent>
              </Select>
              <p className="text-sm text-muted-foreground">
                Choose whether to create a full key with both LLM and Vector DB access, or just one component
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
                    .filter(region => region.is_active)
                    .map(region => (
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
                      aria-expanded={userSearchOpen}
                      className="w-full justify-between"
                    >
                      {getSelectedUserDisplay()}
                      <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
                    </Button>
                  </PopoverTrigger>
                  <PopoverContent className="w-full p-0" align="start">
                    <Command>
                      <CommandInput
                        placeholder="Search users..."
                        value={userSearchTerm}
                        onValueChange={setUserSearchTerm}
                      />
                      <CommandList>
                        <CommandEmpty>No users found.</CommandEmpty>
                        <CommandGroup>
                          {currentUser?.team_id && (
                            <CommandItem
                              value="team"
                              onSelect={() => {
                                setSelectedUserId("team")
                                setUserSearchOpen(false)
                                setUserSearchTerm("")
                              }}
                            >
                              <Check
                                className={cn(
                                  "mr-2 h-4 w-4",
                                  selectedUserId === "team" ? "opacity-100" : "opacity-0"
                                )}
                              />
                              Team (Shared)
                            </CommandItem>
                          )}
                          <CommandItem
                            value={currentUser?.email || "self"}
                            onSelect={() => {
                              setSelectedUserId(currentUser?.id.toString() || "self")
                              setUserSearchOpen(false)
                              setUserSearchTerm("")
                            }}
                          >
                            <Check
                              className={cn(
                                "mr-2 h-4 w-4",
                                selectedUserId === currentUser?.id.toString() || selectedUserId === "self" ? "opacity-100" : "opacity-0"
                              )}
                            />
                            {currentUser?.email || "Me"}
                          </CommandItem>
                          {filteredTeamMembers
                            .filter(member => member.id !== currentUser?.id)
                            .map(member => (
                              <CommandItem
                                key={member.id}
                                value={member.email}
                                onSelect={() => {
                                  setSelectedUserId(member.id.toString())
                                  setUserSearchOpen(false)
                                  setUserSearchTerm("")
                                }}
                              >
                                <Check
                                  className={cn(
                                    "mr-2 h-4 w-4",
                                    selectedUserId === member.id.toString() ? "opacity-100" : "opacity-0"
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
                  {teamMembers.length > 0
                    ? "Select 'Team (Shared)' to create a key accessible to all team members, or assign to a specific user"
                    : currentUser?.team_id
                      ? "Select 'Team (Shared)' to create a key accessible to all team members, or assign to yourself"
                      : "Assign this key to yourself"
                  }
                </p>
              </div>
            )}
          </div>
          <DialogFooter>
            <Button
              type="submit"
              disabled={isLoading || !isFormValid}
            >
              {isLoading && (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              )}
              Create Key
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
