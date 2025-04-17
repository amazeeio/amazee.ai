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
import { Loader2, Eye, EyeOff, Plus } from 'lucide-react';
import { get, post, del } from '@/utils/api';
import { useAuth } from '@/hooks/use-auth';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';

interface TeamAIKey {
  id: string;
  name: string;
  database_name: string;
  database_host: string;
  database_username: string;
  database_password: string;
  region: string;
  created_at: string;
  owner_id: number;
}

export default function TeamAIKeysPage() {
  const { toast } = useToast();
  const { user } = useAuth();
  const [isAddingKey, setIsAddingKey] = useState(false);
  const [showPassword, setShowPassword] = useState<Record<string, boolean>>({});
  const [newKeyName, setNewKeyName] = useState('');
  const [newKeyRegion, setNewKeyRegion] = useState('');
  const [newKeyValue, setNewKeyValue] = useState('');

  const queryClient = useQueryClient();

  const { data: keys = [], isLoading: isLoadingKeys } = useQuery<TeamAIKey[]>({
    queryKey: ['private-ai-keys', user?.team_id],
    queryFn: async () => {
      const response = await get(`private-ai-keys?team_id=${user?.team_id}`, { credentials: 'include' });
      return response.json();
    },
    enabled: !!user?.team_id,
  });

  const createKeyMutation = useMutation({
    mutationFn: async (data: { name: string; api_key: string }) => {
      const response = await post('private-ai-keys', data, { credentials: 'include' });
      return response.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['private-ai-keys'] });
      setIsAddingKey(false);
      setNewKeyName('');
      setNewKeyValue('');
      toast({
        title: 'Success',
        description: 'AI key added successfully',
      });
    },
    onError: () => {
      toast({
        title: 'Error',
        description: 'Failed to add AI key',
        variant: 'destructive',
      });
    },
  });

  const deleteKeyMutation = useMutation({
    mutationFn: async (keyId: string) => {
      const response = await del(`private-ai-keys/${keyId}`, { credentials: 'include' });
      return response.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['private-ai-keys'] });
      toast({
        title: 'Success',
        description: 'AI key deleted successfully',
      });
    },
    onError: () => {
      toast({
        title: 'Error',
        description: 'Failed to delete AI key',
        variant: 'destructive',
      });
    },
  });

  if (isLoadingKeys) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <Loader2 className="h-8 w-8 animate-spin" />
      </div>
    );
  }

  const handleCreateKey = (e: React.FormEvent) => {
    e.preventDefault();
    createKeyMutation.mutate({
      name: newKeyName,
      api_key: newKeyValue,
    });
  };

  const handleDeleteKey = (keyId: string) => {
    deleteKeyMutation.mutate(keyId);
  };

  const togglePasswordVisibility = (keyId: string) => {
    setShowPassword(prev => ({
      ...prev,
      [keyId]: !prev[keyId]
    }));
  };

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h1 className="text-3xl font-bold">Team AI Keys</h1>
        <Dialog open={isAddingKey} onOpenChange={setIsAddingKey}>
          <DialogTrigger asChild>
            <Button>
              <Plus className="mr-2 h-4 w-4" />
              Add Key
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Create New AI Key</DialogTitle>
              <DialogDescription>
                Create a new AI key for your team. This will generate new database credentials.
              </DialogDescription>
            </DialogHeader>
            <form onSubmit={handleCreateKey}>
              <div className="grid gap-4 py-4">
                <div className="grid gap-2">
                  <label htmlFor="name">Name</label>
                  <Input
                    id="name"
                    value={newKeyName}
                    onChange={(e) => setNewKeyName(e.target.value)}
                    required
                  />
                </div>
                <div className="grid gap-2">
                  <label htmlFor="region">Region</label>
                  <Input
                    id="region"
                    value={newKeyRegion}
                    onChange={(e) => setNewKeyRegion(e.target.value)}
                    required
                  />
                </div>
                <div className="grid gap-2">
                  <label htmlFor="value">Value</label>
                  <Input
                    id="value"
                    value={newKeyValue}
                    onChange={(e) => setNewKeyValue(e.target.value)}
                    required
                  />
                </div>
              </div>
              <DialogFooter>
                <Button type="submit" disabled={createKeyMutation.isPending}>
                  {createKeyMutation.isPending && (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  )}
                  Create Key
                </Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>
      </div>

      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Credentials</TableHead>
              <TableHead>Region</TableHead>
              <TableHead>Created At</TableHead>
              <TableHead>Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {keys.map((key, index) => (
              <TableRow key={key.id ? String(key.id) : `key-${index}`}>
                <TableCell>{key.name}</TableCell>
                <TableCell>
                  <div className="space-y-2">
                    <div className="flex items-center gap-2">
                      <span>Database: {key.database_name}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span>Host: {key.database_host}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span>Username: {key.database_username}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span>Password: </span>
                      <span className="font-mono">
                        {showPassword[key.id ? String(key.id) : `key-${index}`] ? key.database_password : '••••••••'}
                      </span>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => togglePasswordVisibility(key.id ? String(key.id) : `key-${index}`)}
                      >
                        {showPassword[key.id ? String(key.id) : `key-${index}`] ? (
                          <EyeOff className="h-4 w-4" />
                        ) : (
                          <Eye className="h-4 w-4" />
                        )}
                      </Button>
                    </div>
                  </div>
                </TableCell>
                <TableCell>{key.region}</TableCell>
                <TableCell>{new Date(key.created_at).toLocaleString()}</TableCell>
                <TableCell>
                  <AlertDialog>
                    <AlertDialogTrigger asChild>
                      <Button variant="destructive" size="sm">
                        Delete
                      </Button>
                    </AlertDialogTrigger>
                    <AlertDialogContent>
                      <AlertDialogHeader>
                        <AlertDialogTitle>Are you sure?</AlertDialogTitle>
                        <AlertDialogDescription>
                          This will permanently delete the AI key and its associated database. This action cannot be undone.
                        </AlertDialogDescription>
                      </AlertDialogHeader>
                      <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction onClick={() => handleDeleteKey(key.id ? String(key.id) : `key-${index}`)}>
                          Delete
                        </AlertDialogAction>
                      </AlertDialogFooter>
                    </AlertDialogContent>
                  </AlertDialog>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}