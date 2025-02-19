'use client';

import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Alert, AlertDescription } from '@/components/ui/alert';
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
import { Loader2, X } from 'lucide-react';

interface APIToken {
  id: number;
  name: string;
  token?: string;
  created_at: string;
  last_used_at?: string;
}

export default function APITokensPage() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [newTokenName, setNewTokenName] = useState('');
  const [showNewToken, setShowNewToken] = useState<APIToken | null>(null);

  const { data: tokensList = [], isLoading } = useQuery({
    queryKey: ['tokens'],
    queryFn: async () => {
      const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/auth/token`, {
        credentials: 'include',
      });
      if (!response.ok) {
        throw new Error('Failed to fetch tokens');
      }
      return response.json();
    },
  });

  const createMutation = useMutation({
    mutationFn: async (name: string) => {
      const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/auth/token`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        credentials: 'include',
        body: JSON.stringify({ name }),
      });
      if (!response.ok) {
        throw new Error('Failed to create token');
      }
      return response.json();
    },
    onSuccess: (newToken) => {
      queryClient.invalidateQueries({ queryKey: ['tokens'] });
      setShowNewToken(newToken);
      setNewTokenName('');
      toast({
        title: 'Success',
        description: 'Token created successfully',
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

  const deleteMutation = useMutation({
    mutationFn: async (tokenId: number) => {
      const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/auth/token/${tokenId}`, {
        method: 'DELETE',
        credentials: 'include',
      });
      if (!response.ok) {
        throw new Error('Failed to delete token');
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tokens'] });
      toast({
        title: 'Success',
        description: 'Token deleted successfully',
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

  const handleCreateToken = async (e: React.FormEvent) => {
    e.preventDefault();
    if (newTokenName.trim()) {
      await createMutation.mutateAsync(newTokenName);
    }
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <Loader2 className="h-8 w-8 animate-spin" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h1 className="text-3xl font-bold">API Tokens</h1>
      </div>

      {/* New Token Form */}
      <Card>
        <CardHeader>
          <CardTitle>Create New Token</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleCreateToken} className="flex gap-4">
            <Input
              type="text"
              value={newTokenName}
              onChange={(e) => setNewTokenName(e.target.value)}
              placeholder="Token name"
              className="max-w-sm"
            />
            <Button
              type="submit"
              disabled={createMutation.isPending || !newTokenName.trim()}
            >
              {createMutation.isPending ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Creating...
                </>
              ) : (
                'Create Token'
              )}
            </Button>
          </form>
        </CardContent>
      </Card>

      {/* Show New Token */}
      {showNewToken && (
        <Alert className="bg-green-50 border-green-200 text-green-800">
          <div className="flex justify-between items-start">
            <div>
              <h3 className="font-medium">New Token Created</h3>
              <AlertDescription className="text-green-700 mt-1">
                Make sure to copy your token now. You won&apos;t be able to see it again!
              </AlertDescription>
            </div>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setShowNewToken(null)}
              className="text-green-700 hover:text-green-900 hover:bg-green-100"
            >
              <X className="h-4 w-4" />
            </Button>
          </div>
          <code className="block p-2 mt-4 bg-white rounded border border-green-200 text-sm">
            {showNewToken.token}
          </code>
        </Alert>
      )}

      {/* Tokens List */}
      <div className="grid gap-4">
        {tokensList.map((token: APIToken) => (
          <Card key={token.id}>
            <CardContent className="pt-6">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="text-lg font-medium">{token.name}</h3>
                  <div className="text-sm text-muted-foreground space-y-1">
                    <p>Created: {new Date(token.created_at).toLocaleDateString()}</p>
                    {token.last_used_at && (
                      <p>Last used: {new Date(token.last_used_at).toLocaleDateString()}</p>
                    )}
                  </div>
                </div>
                <AlertDialog>
                  <AlertDialogTrigger asChild>
                    <Button variant="destructive" size="sm">
                      Delete
                    </Button>
                  </AlertDialogTrigger>
                  <AlertDialogContent>
                    <AlertDialogHeader>
                      <AlertDialogTitle>Delete Token</AlertDialogTitle>
                      <AlertDialogDescription>
                        Are you sure you want to delete this token? This action cannot be undone.
                      </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                      <AlertDialogCancel>Cancel</AlertDialogCancel>
                      <AlertDialogAction
                        onClick={() => deleteMutation.mutate(token.id)}
                        className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                      >
                        {deleteMutation.isPending ? (
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
              </div>
            </CardContent>
          </Card>
        ))}
        {tokensList.length === 0 && (
          <Card>
            <CardContent className="pt-6">
              <p className="text-sm text-muted-foreground">
                Don&apos;t have a token? Contact your administrator.
              </p>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}