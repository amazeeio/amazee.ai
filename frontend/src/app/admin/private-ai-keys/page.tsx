"use client";

import { useState } from "react";
import { CreateAIKeyDialog } from "@/components/create-ai-key-dialog";
import { PrivateAIKeysTable } from "@/components/private-ai-keys-table";
import { usePrivateAIKeysData } from "@/hooks/use-private-ai-keys-data";
import { useToast } from "@/hooks/use-toast";
import { PrivateAIKey } from "@/types/private-ai-key";
import { User } from "@/types/user";
import { get, del, put, post } from "@/utils/api";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { UserFilter } from "./_components/user-filter";
import { Input } from "@/components/ui/input";

export default function PrivateAIKeysPage() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false);
  const [selectedUser, setSelectedUser] = useState<User | null>(null);
  const [dbSearch, setDbSearch] = useState("");

  // Fetch private AI keys based on selected user filter and search
  const { data: privateAIKeys = [], isLoading: isLoadingPrivateAIKeys } =
    useQuery<PrivateAIKey[]>({
      queryKey: ["private-ai-keys", selectedUser?.id, dbSearch],
      queryFn: async () => {
        const params = new URLSearchParams();
        if (selectedUser?.id) {
          params.set("owner_id", String(selectedUser.id));
        }
        if (dbSearch) {
          params.set("search", dbSearch);
        }
        const url = params.toString()
          ? `/private-ai-keys?${params.toString()}`
          : "/private-ai-keys";
        const response = await get(url);
        return response.json();
      },
    });

  // Fetch helper data (team details, members, regions) for the table and dialogs
  const { teamDetails, teamMembers, regions } = usePrivateAIKeysData(
    privateAIKeys,
    new Set(),
  );

  // Mutations
  const createKeyMutation = useMutation({
    mutationFn: async (data: {
      name: string;
      region_id: number;
      key_type: "full" | "llm" | "vector";
      owner_id?: number;
      team_id?: number;
    }) => {
      const endpoint =
        data.key_type === "full"
          ? "/private-ai-keys"
          : data.key_type === "llm"
            ? "/private-ai-keys/token"
            : "/private-ai-keys/vector-db";
      const response = await post(endpoint, data);
      return response.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["private-ai-keys"] });
      setIsCreateDialogOpen(false);
      toast({
        title: "Success",
        description: "Private AI key created successfully",
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

  const deletePrivateAIKeyMutation = useMutation({
    mutationFn: async (keyId: number) => {
      const response = await del(`/private-ai-keys/${keyId}`);
      return response.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["private-ai-keys"] });
      toast({
        title: "Success",
        description: "Private AI key deleted successfully",
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

  const updateBudgetPeriodMutation = useMutation({
    mutationFn: async ({
      keyId,
      budgetDuration,
    }: {
      keyId: number;
      budgetDuration: string;
    }) => {
      const response = await put(`/private-ai-keys/${keyId}/budget-period`, {
        budget_duration: budgetDuration,
      });
      return response.json();
    },
    onSuccess: (data, variables) => {
      queryClient.invalidateQueries({
        queryKey: ["private-ai-key-spend", variables.keyId],
      });
      toast({
        title: "Success",
        description: "Budget period updated successfully",
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

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h1 className="text-3xl font-bold">Private AI Keys</h1>
        <CreateAIKeyDialog
          open={isCreateDialogOpen}
          onOpenChange={setIsCreateDialogOpen}
          onSubmit={createKeyMutation.mutate}
          isLoading={createKeyMutation.isPending}
          regions={regions}
          teamMembers={teamMembers}
          showUserAssignment={true}
          currentUser={undefined}
          triggerText="Create Key"
          title="Create New Private AI Key"
          description="Create a new private AI key for any user or team."
        />
      </div>

      <div className="flex items-center gap-4">
        <UserFilter
          selectedUser={selectedUser}
          onUserSelect={setSelectedUser}
        />
        <Input
          placeholder="Search DB name or username..."
          value={dbSearch}
          onChange={(e) => setDbSearch(e.target.value)}
          className="w-[250px]"
        />
      </div>

      <PrivateAIKeysTable
        keys={privateAIKeys}
        onDelete={deletePrivateAIKeyMutation.mutate}
        isLoading={isLoadingPrivateAIKeys}
        isDeleting={deletePrivateAIKeyMutation.isPending}
        allowModification={true}
        showOwner={true}
        onUpdateBudget={(keyId, budgetDuration) => {
          updateBudgetPeriodMutation.mutate({ keyId, budgetDuration });
        }}
        isUpdatingBudget={updateBudgetPeriodMutation.isPending}
        teamDetails={teamDetails}
        teamMembers={teamMembers}
      />
    </div>
  );
}
