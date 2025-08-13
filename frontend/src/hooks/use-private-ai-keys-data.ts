import { useQuery } from '@tanstack/react-query';
import { get } from '@/utils/api';
import { PrivateAIKey } from '@/types/private-ai-key';

interface TeamUser {
  id: number;
  email: string;
  is_active: boolean;
  role: string;
  team_id: number | null;
  created_at: string;
}

interface Region {
  id: number;
  name: string;
  is_active: boolean;
}

interface SpendInfo {
  spend: number;
  expires: string;
  created_at: string;
  updated_at: string;
  max_budget: number | null;
  budget_duration: string | null;
  budget_reset_at: string | null;
}

export function usePrivateAIKeysData(
  keys: PrivateAIKey[],
  loadedSpendKeys: Set<number>
) {
  // Get unique team IDs from the keys
  const teamIds = Array.from(new Set(keys.filter(key => key.team_id).map(key => key.team_id)));

  // Fetch team details for each team ID
  const { data: teamDetails = {} } = useQuery({
    queryKey: ['team-details', teamIds],
    queryFn: async () => {
      const teamPromises = teamIds.map(async (teamId) => {
        const response = await get(`teams/${teamId}`);
        const data = await response.json();
        return [teamId, data];
      });
      const teamResults = await Promise.all(teamPromises);
      return Object.fromEntries(teamResults);
    },
    enabled: teamIds.length > 0,
  });

  // Query to get all users for displaying emails
  const { data: usersMap = {} } = useQuery<Record<number, { id: number; email: string }>>({
    queryKey: ['users-map'],
    queryFn: async () => {
      const response = await get('users');
      const users: TeamUser[] = await response.json();
      return users.reduce((acc: Record<number, { id: number; email: string }>, user: TeamUser) => ({
        ...acc,
        [user.id]: { id: user.id, email: user.email }
      }), {});
    },
  });

  // Query to get spend information for loaded keys
  // Use a stable query key to avoid Rules of Hooks violations
  const loadedSpendKeysArray = Array.from(loadedSpendKeys).sort();
  const { data: spendMap = {} } = useQuery<Record<number, SpendInfo>>({
    queryKey: ['private-ai-keys-spend', loadedSpendKeysArray],
    queryFn: async () => {
      const spendPromises = loadedSpendKeysArray.map(async (keyId) => {
        const response = await get(`private-ai-keys/${keyId}/spend`);
        return [keyId, await response.json()] as [number, SpendInfo];
      });
      const spendResults = await Promise.all(spendPromises);
      return Object.fromEntries(spendResults);
    },
    enabled: loadedSpendKeysArray.length > 0,
  });

  // Fetch regions
  const { data: regions = [] } = useQuery<Region[]>({
    queryKey: ['regions'],
    queryFn: async () => {
      const response = await get('regions');
      const data = await response.json();
      return data;
    },
  });

  return {
    teamDetails,
    teamMembers: Object.values(usersMap),
    spendMap,
    regions,
  };
}
