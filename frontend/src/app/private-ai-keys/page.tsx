'use client';

import { useState, useRef, useEffect, useCallback } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
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
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { useToast } from '@/hooks/use-toast';
import { Loader2, Eye, EyeOff } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { get, post } from '@/utils/api';

interface Region {
  id: number;
  name: string;
  is_active: boolean;
}

interface PrivateAIKey {
  database_name: string;
  name: string;
  database_host: string;
  database_username: string;
  database_password: string;
  litellm_token: string;
  litellm_api_url: string;
  region: string;
}

export default function DashboardPage() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [visibleCredentials, setVisibleCredentials] = useState<Set<string>>(new Set());
  const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false);
  const [selectedRegion, setSelectedRegion] = useState<string>('');
  const [keyName, setKeyName] = useState<string>('');
  const [newKeyName, setNewKeyName] = useState<string | null>(null);
  const newKeyRef = useRef<HTMLTableRowElement>(null);
  const [regions, setRegions] = useState<Region[]>([]);
  const [privateAIKeys, setPrivateAIKeys] = useState<PrivateAIKey[]>([]);

  // Fetch regions
  const fetchRegions = useCallback(async () => {
    try {
      const response = await get('regions');
      const data = await response.json();
      setRegions(data);
    } catch (error) {
      console.error('Error fetching regions:', error);
      toast({
        title: 'Error',
        description: 'Failed to fetch regions',
        variant: 'destructive',
      });
    }
  }, [toast, setRegions]);

  // Fetch private AI keys
  const fetchKeys = useCallback(async () => {
    try {
      const response = await get('/private-ai-keys');
      const data = await response.json();
      setPrivateAIKeys(data);
    } catch (error) {
      console.error('Error fetching private AI keys:', error);
      toast({
        title: 'Error',
        description: 'Failed to fetch private AI keys',
        variant: 'destructive',
      });
    }
  }, [toast, setPrivateAIKeys]);

  useEffect(() => {
    if (newKeyName && newKeyRef.current) {
      // First scroll attempt
      setTimeout(() => {
        if (newKeyRef.current) {
          newKeyRef.current.scrollIntoView({
            behavior: 'smooth',
            block: 'center'
          });
        }
      }, 500);

      const timer = setTimeout(() => setNewKeyName(null), 5000);
      return () => clearTimeout(timer);
    }
  }, [newKeyName]);

  // Create key mutation
  const createKeyMutation = useMutation({
    mutationFn: async ({ region_id, name }: { region_id: number, name: string }) => {
      const response = await post('/private-ai-keys', { region_id, name });
      const data = await response.json();
      return data;
    },
    onSuccess: async (data) => {
      await queryClient.invalidateQueries({ queryKey: ['private-ai-keys'] });
      setIsCreateDialogOpen(false);
      setSelectedRegion('');
      setKeyName('');
      // Small delay to ensure the data is refetched before we set the new key name
      setTimeout(() => {
        setNewKeyName(data.database_name);
      }, 100);
      toast({
        title: 'Success',
        description: 'Private AI key created successfully',
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

  const toggleCredentialVisibility = (key: string) => {
    setVisibleCredentials(prev => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  };

  // Group keys by region
  const keysByRegion = (privateAIKeys as PrivateAIKey[]).reduce<Record<string, PrivateAIKey[]>>((acc, key) => {
    if (!acc[key.region]) {
      acc[key.region] = [];
    }
    acc[key.region].push(key);
    return acc;
  }, {});

  const handleCreateKey = () => {
    if (!selectedRegion || !keyName) return;

    const region = regions.find(r => r.name === selectedRegion);
    if (!region) return;

    createKeyMutation.mutate({
      region_id: region.id,
      name: keyName
    });
  };

  useEffect(() => {
    void fetchRegions();
    void fetchKeys();
  }, [fetchRegions, fetchKeys]);

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h1 className="text-3xl font-bold">Private AI Keys</h1>
        <Dialog open={isCreateDialogOpen} onOpenChange={setIsCreateDialogOpen}>
          <DialogTrigger asChild>
            <Button>Create Private AI Key</Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Create Private AI Key</DialogTitle>
              <DialogDescription>
                Select a region and provide a name for your new private AI key.
              </DialogDescription>
            </DialogHeader>
            <div className="py-4 space-y-4">
              <div className="space-y-2">
                <label className="text-sm font-medium">Name <span className="text-red-500">*</span></label>
                <Input
                  value={keyName}
                  onChange={(e) => setKeyName(e.target.value)}
                  placeholder="My AI Key"
                  required
                />
                <p className="text-sm text-muted-foreground">
                  A descriptive name to help you identify this key
                </p>
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium">Region <span className="text-red-500">*</span></label>
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
            </div>
            <DialogFooter>
              <Button
                onClick={handleCreateKey}
                disabled={!selectedRegion || !keyName || createKeyMutation.isPending}
              >
                {createKeyMutation.isPending ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    Creating...
                  </>
                ) : (
                  'Create Key'
                )}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>

      {Object.entries(keysByRegion).map(([region, keys]) => (
        <Card key={region}>
          <CardHeader>
            <CardTitle>Region: {region}</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Name</TableHead>
                    <TableHead>VectorDB Credentials</TableHead>
                    <TableHead>LLM Credentials</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {keys.map((key) => {
                    return (
                      <TableRow
                        key={key.database_name}
                        ref={key.database_name === newKeyName ? newKeyRef : null}
                        className={`scroll-mt-32 ${
                          key.database_name === newKeyName
                            ? 'bg-green-100 dark:bg-green-900/20 shadow-[0_0_15px_rgba(0,128,0,0.2)] transition-all duration-1000'
                            : ''
                        }`}
                      >
                        <TableCell>{key.name || key.database_name}</TableCell>
                        <TableCell>
                          <div className="space-y-2">
                            <div className="flex items-center gap-2">
                              <span>DB Name: {key.database_name}</span>
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
                                      onClick={() => toggleCredentialVisibility(`${key.database_name}-password`)}
                                    >
                                      <EyeOff className="h-4 w-4" />
                                    </Button>
                                  </div>
                                ) : (
                                  <Button
                                    variant="ghost"
                                    size="icon"
                                    onClick={() => toggleCredentialVisibility(`${key.database_name}-password`)}
                                  >
                                    <Eye className="h-4 w-4" />
                                  </Button>
                                )}
                              </div>
                            )}
                          </div>
                        </TableCell>
                        <TableCell>
                          <div className="space-y-2">
                            <div className="flex items-center gap-2">
                              <span>API URL: {key.litellm_api_url}</span>
                            </div>
                            {key.litellm_token && (
                              <div className="flex items-center gap-2">
                                <span>API Key:</span>
                                {visibleCredentials.has(`${key.database_name}-token`) ? (
                                  <div className="flex items-center gap-2">
                                    <code className="px-2 py-1 bg-muted rounded text-sm font-mono">
                                      {key.litellm_token}
                                    </code>
                                    <Button
                                      variant="ghost"
                                      size="icon"
                                      onClick={() => toggleCredentialVisibility(`${key.database_name}-token`)}
                                    >
                                      <EyeOff className="h-4 w-4" />
                                    </Button>
                                  </div>
                                ) : (
                                  <Button
                                    variant="ghost"
                                    size="icon"
                                    onClick={() => toggleCredentialVisibility(`${key.database_name}-token`)}
                                  >
                                    <Eye className="h-4 w-4" />
                                  </Button>
                                )}
                              </div>
                            )}
                          </div>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          </CardContent>
        </Card>
      ))}

      {privateAIKeys.length === 0 && (
        <Card>
          <CardContent className="p-6">
            <p className="text-center text-muted-foreground">
              No private AI keys found. Keys will appear here once they are created.
            </p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}