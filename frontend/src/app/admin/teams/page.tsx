'use client';

import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
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
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { useToast } from '@/hooks/use-toast';
import { Loader2, Plus, ChevronDown, ChevronRight, UserPlus } from 'lucide-react';
import { get, post, put } from '@/utils/api';
import {
  Collapsible,
  CollapsibleContent,
} from '@/components/ui/collapsible';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import React from 'react';

interface TeamUser {
  id: number;
  email: string;
  is_active: boolean;
  is_admin: boolean;
  role: string;
}

interface Team {
  id: string;
  name: string;
  email: string;
  phone: string;
  billing_address: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  users?: TeamUser[];
}

interface User {
  id: number;
  email: string;
  is_active: boolean;
  is_admin: boolean;
}

export default function TeamsPage() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [isAddingTeam, setIsAddingTeam] = useState(false);
  const [newTeamName, setNewTeamName] = useState('');
  const [newTeamEmail, setNewTeamEmail] = useState('');
  const [newTeamPhone, setNewTeamPhone] = useState('');
  const [newTeamBillingAddress, setNewTeamBillingAddress] = useState('');
  const [expandedTeamId, setExpandedTeamId] = useState<string | null>(null);
  const [isAddingUserToTeam, setIsAddingUserToTeam] = useState(false);
  const [isCreatingUserInTeam, setIsCreatingUserInTeam] = useState(false);
  const [selectedTeamId, setSelectedTeamId] = useState<string | null>(null);
  const [newUserEmail, setNewUserEmail] = useState('');
  const [newUserPassword, setNewUserPassword] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<User[]>([]);
  const [isSearching, setIsSearching] = useState(false);

  // Queries
  const { data: teams = [], isLoading: isLoadingTeams } = useQuery<Team[]>({
    queryKey: ['teams'],
    queryFn: async () => {
      const response = await get('/teams');
      const data = await response.json();
      return data;
    },
  });

  // Get team details when expanded
  const { data: expandedTeam, isLoading: isLoadingTeamDetails } = useQuery<Team>({
    queryKey: ['team', expandedTeamId],
    queryFn: async () => {
      if (!expandedTeamId) return null;
      const response = await get(`/teams/${expandedTeamId}`);
      return response.json();
    },
    enabled: !!expandedTeamId,
  });

  // Search users query
  const searchUsersMutation = useMutation({
    mutationFn: async (query: string) => {
      if (!query) return [];
      const response = await get(`/users/search?email=${encodeURIComponent(query)}`);
      return response.json();
    },
    onSuccess: (data) => {
      setSearchResults(data);
      setIsSearching(false);
    },
    onError: (error: Error) => {
      toast({
        title: 'Error',
        description: error.message,
        variant: 'destructive',
      });
      setIsSearching(false);
    },
  });

  // Mutations
  const createTeamMutation = useMutation({
    mutationFn: async (teamData: {
      name: string;
      email: string;
      phone: string;
      billing_address: string;
    }) => {
      try {
        const response = await post('/teams', teamData);
        return response.json();
      } catch (error) {
        // Handle different types of errors
        if (error instanceof Error) {
          // If it's a network error or other error with a message
          throw new Error(`Failed to create team: ${error.message}`);
        } else if (typeof error === 'object' && error !== null && 'status' in error) {
          // If it's a response error with status
          const status = (error as { status: number }).status;
          if (status === 500) {
            throw new Error('Server error: Failed to create team. Please try again later.');
          } else if (status === 400) {
            throw new Error('Invalid team data. Please check your inputs and try again.');
          } else if (status === 409) {
            throw new Error('A team with this email already exists.');
          } else {
            throw new Error(`Failed to create team (Status: ${status})`);
          }
        } else {
          // Generic error
          throw new Error('An unexpected error occurred while creating the team.');
        }
      }
    },
    onSuccess: () => {
      // Invalidate and refetch the teams query to reload the list
      queryClient.invalidateQueries({ queryKey: ['teams'] });
      // Force a refetch to ensure we have the latest data
      queryClient.refetchQueries({ queryKey: ['teams'] });

      toast({
        title: 'Success',
        description: 'Team created successfully',
      });
      setIsAddingTeam(false);
      setNewTeamName('');
      setNewTeamEmail('');
      setNewTeamPhone('');
      setNewTeamBillingAddress('');
    },
    onError: (error: Error) => {
      toast({
        title: 'Error',
        description: error.message,
        variant: 'destructive',
      });
    },
  });

  const createUserMutation = useMutation({
    mutationFn: async (userData: { email: string; password: string; team_id?: number }) => {
      try {
        const response = await post('/users', userData);
        return response.json();
      } catch (error) {
        if (error instanceof Error) {
          throw new Error(`Failed to create user: ${error.message}`);
        } else {
          throw new Error('An unexpected error occurred while creating the user.');
        }
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['team', selectedTeamId] });
      queryClient.invalidateQueries({ queryKey: ['teams'] });

      toast({
        title: 'Success',
        description: 'User created successfully',
      });
      setIsCreatingUserInTeam(false);
      setNewUserEmail('');
      setNewUserPassword('');
    },
    onError: (error: Error) => {
      toast({
        title: 'Error',
        description: error.message,
        variant: 'destructive',
      });
    },
  });

  const addUserToTeamMutation = useMutation({
    mutationFn: async ({ userId, teamId }: { userId: number; teamId: string }) => {
      try {
        const response = await post(`/users/${userId}/add-to-team`, { team_id: parseInt(teamId) });
        return response.json();
      } catch (error) {
        if (error instanceof Error) {
          throw new Error(`Failed to add user to team: ${error.message}`);
        } else {
          throw new Error('An unexpected error occurred while adding the user to the team.');
        }
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['team', selectedTeamId] });
      queryClient.invalidateQueries({ queryKey: ['teams'] });

      toast({
        title: 'Success',
        description: 'User added to team successfully',
      });
      setIsAddingUserToTeam(false);
      setSearchQuery('');
      setSearchResults([]);
    },
    onError: (error: Error) => {
      toast({
        title: 'Error',
        description: error.message,
        variant: 'destructive',
      });
    },
  });

  const removeUserFromTeamMutation = useMutation({
    mutationFn: async (userId: number) => {
      try {
        const response = await post(`/users/${userId}/remove-from-team`, {});
        return response.json();
      } catch (error) {
        if (error instanceof Error) {
          throw new Error(`Failed to remove user from team: ${error.message}`);
        } else {
          throw new Error('An unexpected error occurred while removing the user from the team.');
        }
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['team', selectedTeamId] });
      queryClient.invalidateQueries({ queryKey: ['teams'] });

      toast({
        title: 'Success',
        description: 'User removed from team successfully',
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

  const handleCreateTeam = (e: React.FormEvent) => {
    e.preventDefault();
    createTeamMutation.mutate({
      name: newTeamName,
      email: newTeamEmail,
      phone: newTeamPhone,
      billing_address: newTeamBillingAddress,
    });
  };

  const handleCreateUserInTeam = (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedTeamId) return;

    createUserMutation.mutate({
      email: newUserEmail,
      password: newUserPassword,
      team_id: parseInt(selectedTeamId),
    });
  };

  const handleAddUserToTeam = (userId: number, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!selectedTeamId) return;

    addUserToTeamMutation.mutate({
      userId,
      teamId: selectedTeamId,
    });
  };

  const handleRemoveUserFromTeam = (userId: number, e: React.MouseEvent) => {
    e.stopPropagation();
    removeUserFromTeamMutation.mutate(userId);
  };

  const handleSearchUsers = (e: React.FormEvent) => {
    e.preventDefault();
    if (!searchQuery.trim()) return;

    setIsSearching(true);
    searchUsersMutation.mutate(searchQuery);
  };

  const toggleTeamExpansion = (teamId: string) => {
    if (expandedTeamId === teamId) {
      setExpandedTeamId(null);
    } else {
      setExpandedTeamId(teamId);
    }
  };

  const openAddUserToTeamDialog = (teamId: string) => {
    setSelectedTeamId(teamId);
    setIsAddingUserToTeam(true);
  };

  const openCreateUserInTeamDialog = (teamId: string) => {
    setSelectedTeamId(teamId);
    setIsCreatingUserInTeam(true);
  };

  return (
    <div className="container mx-auto py-10">
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-3xl font-bold">Teams</h1>
        <Dialog open={isAddingTeam} onOpenChange={setIsAddingTeam}>
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
                Create a new team by filling out the information below.
              </DialogDescription>
            </DialogHeader>
            <form onSubmit={handleCreateTeam}>
              <div className="grid gap-4 py-4">
                <div className="grid grid-cols-4 items-center gap-4">
                  <label htmlFor="name" className="text-right">
                    Name
                  </label>
                  <Input
                    id="name"
                    value={newTeamName}
                    onChange={(e) => setNewTeamName(e.target.value)}
                    className="col-span-3"
                    required
                  />
                </div>
                <div className="grid grid-cols-4 items-center gap-4">
                  <label htmlFor="email" className="text-right">
                    Email
                  </label>
                  <Input
                    id="email"
                    type="email"
                    value={newTeamEmail}
                    onChange={(e) => setNewTeamEmail(e.target.value)}
                    className="col-span-3"
                    required
                  />
                </div>
                <div className="grid grid-cols-4 items-center gap-4">
                  <label htmlFor="phone" className="text-right">
                    Phone
                  </label>
                  <Input
                    id="phone"
                    value={newTeamPhone}
                    onChange={(e) => setNewTeamPhone(e.target.value)}
                    className="col-span-3"
                    required
                  />
                </div>
                <div className="grid grid-cols-4 items-center gap-4">
                  <label htmlFor="billing_address" className="text-right">
                    Billing Address
                  </label>
                  <Input
                    id="billing_address"
                    value={newTeamBillingAddress}
                    onChange={(e) => setNewTeamBillingAddress(e.target.value)}
                    className="col-span-3"
                    required
                  />
                </div>
              </div>
              <DialogFooter>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => setIsAddingTeam(false)}
                >
                  Cancel
                </Button>
                <Button
                  type="submit"
                  disabled={createTeamMutation.isPending}
                >
                  {createTeamMutation.isPending && (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  )}
                  Create Team
                </Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>
      </div>

      {isLoadingTeams ? (
        <div className="flex justify-center items-center h-64">
          <Loader2 className="h-8 w-8 animate-spin" />
        </div>
      ) : (
        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-10"></TableHead>
                <TableHead>Name</TableHead>
                <TableHead>Email</TableHead>
                <TableHead>Phone</TableHead>
                <TableHead>Billing Address</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Created At</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {teams.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={7} className="text-center py-6">
                    No teams found. Create a new team to get started.
                  </TableCell>
                </TableRow>
              ) : (
                teams.map((team) => (
                  <React.Fragment key={team.id}>
                    <TableRow
                      className="cursor-pointer hover:bg-muted/50"
                      onClick={() => toggleTeamExpansion(team.id)}
                    >
                      <TableCell>
                        {expandedTeamId === team.id ? (
                          <ChevronDown className="h-4 w-4" />
                        ) : (
                          <ChevronRight className="h-4 w-4" />
                        )}
                      </TableCell>
                      <TableCell className="font-medium">{team.name}</TableCell>
                      <TableCell>{team.email}</TableCell>
                      <TableCell>{team.phone}</TableCell>
                      <TableCell>{team.billing_address}</TableCell>
                      <TableCell>
                        <span
                          className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${
                            team.is_active
                              ? 'bg-green-100 text-green-800'
                              : 'bg-red-100 text-red-800'
                          }`}
                        >
                          {team.is_active ? 'Active' : 'Inactive'}
                        </span>
                      </TableCell>
                      <TableCell>
                        {new Date(team.created_at).toLocaleDateString()}
                      </TableCell>
                    </TableRow>
                    {expandedTeamId === team.id && (
                      <TableRow>
                        <TableCell colSpan={7} className="p-0">
                          <Collapsible open={expandedTeamId === team.id}>
                            <CollapsibleContent className="p-4 bg-muted/30">
                              {isLoadingTeamDetails ? (
                                <div className="flex justify-center items-center py-8">
                                  <Loader2 className="h-8 w-8 animate-spin" />
                                </div>
                              ) : expandedTeam ? (
                                <div className="space-y-6">
                                  <Tabs defaultValue="details">
                                    <TabsList>
                                      <TabsTrigger value="details">Team Details</TabsTrigger>
                                      <TabsTrigger value="users">Users</TabsTrigger>
                                    </TabsList>
                                    <TabsContent value="details" className="mt-4">
                                      <Card>
                                        <CardHeader>
                                          <CardTitle>Team Information</CardTitle>
                                          <CardDescription>
                                            Detailed information about the team
                                          </CardDescription>
                                        </CardHeader>
                                        <CardContent className="space-y-4">
                                          <div className="grid grid-cols-2 gap-4">
                                            <div>
                                              <p className="text-sm font-medium text-muted-foreground">Name</p>
                                              <p>{expandedTeam.name}</p>
                                            </div>
                                            <div>
                                              <p className="text-sm font-medium text-muted-foreground">Email</p>
                                              <p>{expandedTeam.email}</p>
                                            </div>
                                            <div>
                                              <p className="text-sm font-medium text-muted-foreground">Phone</p>
                                              <p>{expandedTeam.phone}</p>
                                            </div>
                                            <div>
                                              <p className="text-sm font-medium text-muted-foreground">Billing Address</p>
                                              <p>{expandedTeam.billing_address}</p>
                                            </div>
                                            <div>
                                              <p className="text-sm font-medium text-muted-foreground">Status</p>
                                              <Badge variant={expandedTeam.is_active ? "default" : "destructive"}>
                                                {expandedTeam.is_active ? "Active" : "Inactive"}
                                              </Badge>
                                            </div>
                                            <div>
                                              <p className="text-sm font-medium text-muted-foreground">Created At</p>
                                              <p>{new Date(expandedTeam.created_at).toLocaleString()}</p>
                                            </div>
                                            {expandedTeam.updated_at && (
                                              <div>
                                                <p className="text-sm font-medium text-muted-foreground">Updated At</p>
                                                <p>{new Date(expandedTeam.updated_at).toLocaleString()}</p>
                                              </div>
                                            )}
                                          </div>
                                        </CardContent>
                                      </Card>
                                    </TabsContent>
                                    <TabsContent value="users" className="mt-4">
                                      <div className="flex justify-between items-center mb-4">
                                        <h3 className="text-lg font-medium">Team Users</h3>
                                        <div className="space-x-2">
                                          <Button
                                            size="sm"
                                            onClick={() => openAddUserToTeamDialog(team.id)}
                                          >
                                            <UserPlus className="mr-2 h-4 w-4" />
                                            Add Existing User
                                          </Button>
                                          <Button
                                            size="sm"
                                            onClick={() => openCreateUserInTeamDialog(team.id)}
                                          >
                                            <Plus className="mr-2 h-4 w-4" />
                                            Create New User
                                          </Button>
                                        </div>
                                      </div>

                                      {expandedTeam.users && expandedTeam.users.length > 0 ? (
                                        <div className="rounded-md border">
                                          <Table>
                                            <TableHeader>
                                              <TableRow>
                                                <TableHead>Email</TableHead>
                                                <TableHead>Role</TableHead>
                                                <TableHead>Status</TableHead>
                                                <TableHead>Admin</TableHead>
                                                <TableHead className="text-right">Actions</TableHead>
                                              </TableRow>
                                            </TableHeader>
                                            <TableBody>
                                              {expandedTeam.users.map((user) => (
                                                <TableRow key={user.id}>
                                                  <TableCell>{user.email}</TableCell>
                                                  <TableCell>{user.role || 'User'}</TableCell>
                                                  <TableCell>
                                                    <Badge variant={user.is_active ? "default" : "destructive"}>
                                                      {user.is_active ? "Active" : "Inactive"}
                                                    </Badge>
                                                  </TableCell>
                                                  <TableCell>
                                                    <Badge variant={user.is_admin ? "default" : "outline"}>
                                                      {user.is_admin ? "Yes" : "No"}
                                                    </Badge>
                                                  </TableCell>
                                                  <TableCell className="text-right">
                                                    <Button
                                                      size="sm"
                                                      variant="destructive"
                                                      onClick={(e) => handleRemoveUserFromTeam(user.id, e)}
                                                      disabled={removeUserFromTeamMutation.isPending}
                                                    >
                                                      Remove
                                                    </Button>
                                                  </TableCell>
                                                </TableRow>
                                              ))}
                                            </TableBody>
                                          </Table>
                                        </div>
                                      ) : (
                                        <div className="text-center py-8 border rounded-md">
                                          <p className="text-muted-foreground">No users in this team yet.</p>
                                          <p className="text-sm text-muted-foreground mt-2">
                                            Add existing users or create new ones to get started.
                                          </p>
                                        </div>
                                      )}
                                    </TabsContent>
                                  </Tabs>
                                </div>
                              ) : (
                                <div className="text-center py-8">
                                  <p className="text-muted-foreground">Failed to load team details.</p>
                                </div>
                              )}
                            </CollapsibleContent>
                          </Collapsible>
                        </TableCell>
                      </TableRow>
                    )}
                  </React.Fragment>
                ))
              )}
            </TableBody>
          </Table>
        </div>
      )}

      {/* Dialog for adding existing users to a team */}
      <Dialog open={isAddingUserToTeam} onOpenChange={setIsAddingUserToTeam}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add User to Team</DialogTitle>
            <DialogDescription>
              Search for an existing user to add to this team.
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleSearchUsers} className="space-y-4">
            <div className="flex space-x-2">
              <Input
                placeholder="Search by email..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="flex-1"
              />
              <Button type="submit" disabled={isSearching}>
                {isSearching ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  "Search"
                )}
              </Button>
            </div>
          </form>

          {searchResults.length > 0 && (
            <div className="mt-4">
              <h4 className="text-sm font-medium mb-2">Search Results</h4>
              <div className="rounded-md border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Email</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>Admin</TableHead>
                      <TableHead className="w-20"></TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {searchResults.map((user) => (
                      <TableRow key={user.id}>
                        <TableCell>{user.email}</TableCell>
                        <TableCell>
                          <Badge variant={user.is_active ? "default" : "destructive"}>
                            {user.is_active ? "Active" : "Inactive"}
                          </Badge>
                        </TableCell>
                        <TableCell>
                          <Badge variant={user.is_admin ? "default" : "outline"}>
                            {user.is_admin ? "Yes" : "No"}
                          </Badge>
                        </TableCell>
                        <TableCell>
                          <Button
                            size="sm"
                            onClick={(e) => {
                              e.stopPropagation();
                              handleAddUserToTeam(user.id, e);
                            }}
                            disabled={addUserToTeamMutation.isPending}
                          >
                            {addUserToTeamMutation.isPending ? (
                              <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                              "Add"
                            )}
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </div>
          )}

          {searchQuery && !isSearching && searchResults.length === 0 && (
            <div className="text-center py-4">
              <p className="text-muted-foreground">No users found matching your search.</p>
            </div>
          )}

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                setIsAddingUserToTeam(false);
                setSearchQuery('');
                setSearchResults([]);
              }}
            >
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Dialog for creating a new user in a team */}
      <Dialog open={isCreatingUserInTeam} onOpenChange={setIsCreatingUserInTeam}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create New User</DialogTitle>
            <DialogDescription>
              Create a new user and add them to this team.
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleCreateUserInTeam}>
            <div className="grid gap-4 py-4">
              <div className="grid grid-cols-4 items-center gap-4">
                <label htmlFor="new-user-email" className="text-right">
                  Email
                </label>
                <Input
                  id="new-user-email"
                  type="email"
                  value={newUserEmail}
                  onChange={(e) => setNewUserEmail(e.target.value)}
                  className="col-span-3"
                  required
                />
              </div>
              <div className="grid grid-cols-4 items-center gap-4">
                <label htmlFor="new-user-password" className="text-right">
                  Password
                </label>
                <Input
                  id="new-user-password"
                  type="password"
                  value={newUserPassword}
                  onChange={(e) => setNewUserPassword(e.target.value)}
                  className="col-span-3"
                  required
                />
              </div>
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setIsCreatingUserInTeam(false)}
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
    </div>
  );
}