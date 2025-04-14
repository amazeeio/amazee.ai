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
}

export default function TeamsPage() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [isAddingTeam, setIsAddingTeam] = useState(false);
  const [newTeamName, setNewTeamName] = useState('');
  const [newTeamEmail, setNewTeamEmail] = useState('');
  const [newTeamPhone, setNewTeamPhone] = useState('');
  const [newTeamBillingAddress, setNewTeamBillingAddress] = useState('');

  // Queries
  const { data: teams = [], isLoading: isLoadingTeams } = useQuery<Team[]>({
    queryKey: ['teams'],
    queryFn: async () => {
      const response = await get('/teams');
      const data = await response.json();
      return data;
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

  const handleCreateTeam = (e: React.FormEvent) => {
    e.preventDefault();
    createTeamMutation.mutate({
      name: newTeamName,
      email: newTeamEmail,
      phone: newTeamPhone,
      billing_address: newTeamBillingAddress,
    });
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
                  <TableCell colSpan={6} className="text-center py-6">
                    No teams found. Create a new team to get started.
                  </TableCell>
                </TableRow>
              ) : (
                teams.map((team) => (
                  <TableRow key={team.id}>
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
                ))
              )}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}