"use client";

import { Loader2, X } from "lucide-react";
import { useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DeleteConfirmationDialog } from "@/components/ui/delete-confirmation-dialog";
import { Input } from "@/components/ui/input";
import { useToast } from "@/hooks/use-toast";
import { get, post, del } from "@/utils/api";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

interface APIToken {
  id: string;
  name: string;
  token: string;
  created_at: string;
  last_used_at?: string;
  expires_at?: string;
  expiry_option: string;
}

interface ExpiryOption {
  id: number;
  name: string;
  slug: string;
  days: number | null;
}

export default function APITokensPage() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [newTokenName, setNewTokenName] = useState("");
  const [selectedExpiry, setSelectedExpiry] = useState("forever");
  const [showNewToken, setShowNewToken] = useState<APIToken | null>(null);

  const { data: expiryOptions } = useQuery({
    queryKey: ["expiry-options"],
    queryFn: async () => {
      const response = await get("/auth/token/expiry-options");
      return response.json() as Promise<ExpiryOption[]>;
    },
  });

  const { isLoading: tokensLoading, data: tokens = [] } = useQuery({
    queryKey: ["tokens"],
    queryFn: async () => {
      const response = await get("/auth/token");
      const data = await response.json();
      return data as APIToken[];
    },
  });

  const createMutation = useMutation({
    mutationFn: async (payload: { name: string; expiry: string }) => {
      const response = await post("/auth/token", payload);
      const data = await response.json();
      return data;
    },
    onSuccess: (newToken) => {
      queryClient.invalidateQueries({ queryKey: ["tokens"] });
      setShowNewToken(newToken);
      setNewTokenName("");
      setSelectedExpiry("forever");
      toast({
        title: "Success",
        description: "Token created successfully",
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

  const deleteMutation = useMutation({
    mutationFn: async (tokenId: string) => {
      await del(`/auth/token/${tokenId}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tokens"] });
      toast({
        title: "Success",
        description: "Token deleted successfully",
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

  const handleCreateToken = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!newTokenName.trim()) return;
    createMutation.mutate({ name: newTokenName, expiry: selectedExpiry });
  };

  const handleDeleteToken = async (tokenId: string) => {
    deleteMutation.mutate(tokenId);
  };

  if (tokensLoading) {
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
          <form onSubmit={handleCreateToken} className="flex gap-4 items-end">
            <div className="grid gap-2 flex-1 max-w-sm">
              <label htmlFor="token-name" className="text-sm font-medium">
                Token Name
              </label>
              <Input
                id="token-name"
                type="text"
                value={newTokenName}
                onChange={(e) => setNewTokenName(e.target.value)}
                placeholder="Token name"
              />
            </div>
            <div className="grid gap-2 w-[200px]">
              <label htmlFor="expiry" className="text-sm font-medium">
                Expiration
              </label>
              <Select
                value={selectedExpiry}
                onValueChange={setSelectedExpiry}
              >
                <SelectTrigger id="expiry">
                  <SelectValue placeholder="Select expiry" />
                </SelectTrigger>
                <SelectContent>
                  {expiryOptions?.map((opt) => (
                    <SelectItem key={opt.slug} value={opt.slug}>
                      {opt.name}
                    </SelectItem>
                  ))}
                  {(!expiryOptions || expiryOptions.length === 0) && (
                    <SelectItem value="forever">forever</SelectItem>
                  )}
                </SelectContent>
              </Select>
            </div>
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
                "Create Token"
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
                Make sure to copy your token now. You won&apos;t be able to see
                it again!
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
        {tokens.map((token: APIToken) => (
          <Card key={token.id}>
            <CardContent className="pt-6">
              <div className="flex items-center justify-between">
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4 flex-1">
                  <div>
                    <h3 className="text-lg font-medium">{token.name}</h3>
                    <p className="text-xs text-muted-foreground">
                      Created: {new Date(token.created_at).toLocaleDateString()}
                    </p>
                  </div>
                  <div className="flex flex-col justify-center">
                    <p className="text-sm font-medium">Expires</p>
                    <p className="text-xs text-muted-foreground">
                      {token.expires_at
                        ? new Date(token.expires_at).toLocaleDateString()
                        : "Never"}
                    </p>
                  </div>
                  <div className="flex flex-col justify-center">
                    {token.last_used_at && (
                      <>
                        <p className="text-sm font-medium">Last used</p>
                        <p className="text-xs text-muted-foreground">
                          {new Date(token.last_used_at).toLocaleDateString()}
                        </p>
                      </>
                    )}
                  </div>
                </div>
                <DeleteConfirmationDialog
                  title="Delete Token"
                  description="Are you sure you want to delete this token? This action cannot be undone."
                  onConfirm={() => handleDeleteToken(token.id)}
                  isLoading={deleteMutation.isPending}
                />
              </div>
            </CardContent>
          </Card>
        ))}
        {tokens.length === 0 && (
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
