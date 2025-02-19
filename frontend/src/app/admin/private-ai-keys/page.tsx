'use client';

import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
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
import { useToast } from '@/hooks/use-toast';
import { Loader2, Eye, EyeOff } from 'lucide-react';
import { get, del } from '@/utils/api';

interface PrivateAIKey {
  database_name: string;
  name: string;  // User-friendly display name
  database_host: string;
  database_username: string;
  database_password: string;
  litellm_token: string;
  litellm_api_url: string;
  owner_id: number;
  region: string;
}

export default function PrivateAIKeysPage() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [visibleCredentials, setVisibleCredentials] = useState<Set<string>>(new Set());

  // Queries
  const { data: privateAIKeys = [], isLoading: isLoadingPrivateAIKeys } = useQuery<PrivateAIKey[]>({
    queryKey: ['private-ai-keys'],
    queryFn: async () => {
      const response = await get('/private-ai-keys');
      const data = await response.json();
      return data;
    },
  });

  // Mutations
  const deletePrivateAIKeyMutation = useMutation({
    mutationFn: async (keyName: string) => {
      await del(`/private-ai-keys/${keyName}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['private-ai-keys'] });
      toast({
        title: 'Success',
        description: 'Private AI key deleted successfully',
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

  if (isLoadingPrivateAIKeys) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <Loader2 className="h-8 w-8 animate-spin" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h1 className="text-3xl font-bold">Private AI Keys</h1>
      </div>

      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Credentials</TableHead>
              <TableHead>Region</TableHead>
              <TableHead>Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {privateAIKeys.map((key) => (
              <TableRow key={key.database_name}>
                <TableCell>{key.name || key.database_name}</TableCell>
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
                    {key.database_password && (
                      <div className="flex items-center gap-2">
                        <span>Password:</span>
                        {visibleCredentials.has(`${key.database_name}-password`) ? (
                          <div className="flex items-center gap-2">
                            <code className="px-2 py-1 bg-muted rounded text-sm font-mono">
                              {key.database_password}
                            </code>
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={() => {
                                setVisibleCredentials(prev => {
                                  const next = new Set(prev);
                                  next.delete(`${key.database_name}-password`);
                                  return next;
                                });
                              }}
                            >
                              <EyeOff className="h-4 w-4" />
                            </Button>
                          </div>
                        ) : (
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => {
                              setVisibleCredentials(prev => {
                                const next = new Set(prev);
                                next.add(`${key.database_name}-password`);
                                return next;
                              });
                            }}
                          >
                            <Eye className="h-4 w-4" />
                          </Button>
                        )}
                      </div>
                    )}
                    {key.litellm_token && (
                      <div className="flex items-center gap-2">
                        <span>LLM Key:</span>
                        {visibleCredentials.has(`${key.database_name}-token`) ? (
                          <div className="flex items-center gap-2">
                            <code className="px-2 py-1 bg-muted rounded text-sm font-mono">
                              {key.litellm_token}
                            </code>
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={() => {
                                setVisibleCredentials(prev => {
                                  const next = new Set(prev);
                                  next.delete(`${key.database_name}-token`);
                                  return next;
                                });
                              }}
                            >
                              <EyeOff className="h-4 w-4" />
                            </Button>
                          </div>
                        ) : (
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => {
                              setVisibleCredentials(prev => {
                                const next = new Set(prev);
                                next.add(`${key.database_name}-token`);
                                return next;
                              });
                            }}
                          >
                            <Eye className="h-4 w-4" />
                          </Button>
                        )}
                      </div>
                    )}
                  </div>
                </TableCell>
                <TableCell>
                  <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-800">
                    {key.region}
                  </span>
                </TableCell>
                <TableCell>
                  <AlertDialog>
                    <AlertDialogTrigger asChild>
                      <Button variant="destructive" size="sm">Delete</Button>
                    </AlertDialogTrigger>
                    <AlertDialogContent>
                      <AlertDialogHeader>
                        <AlertDialogTitle>Delete Private AI Key</AlertDialogTitle>
                        <AlertDialogDescription>
                          Are you sure you want to delete this private AI key? This action cannot be undone.
                        </AlertDialogDescription>
                      </AlertDialogHeader>
                      <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction
                          onClick={() => deletePrivateAIKeyMutation.mutate(key.database_name)}
                          className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                        >
                          {deletePrivateAIKeyMutation.isPending ? (
                            <>
                              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                              Deleting...
                            </>
                          ) : (
                            'Delete'
                          )}
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