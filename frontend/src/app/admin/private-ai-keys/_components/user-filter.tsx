import { useState, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { Loader2, Search } from 'lucide-react';
import { get } from '@/utils/api';
import { useDebounce } from '@/hooks/use-debounce';
import { useToast } from '@/hooks/use-toast';
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { User } from '@/types/user';

interface UserFilterProps {
  selectedUser: User | null;
  onUserSelect: (user: User | null) => void;
}

export function UserFilter({ selectedUser, onUserSelect }: UserFilterProps) {
  const { toast } = useToast();
  const [isUserSearchOpen, setIsUserSearchOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const debouncedSearchTerm = useDebounce(searchTerm, 300);

  const { data: users = [], isLoading: isSearching, error: searchError } = useQuery<User[]>({
    queryKey: ['users', debouncedSearchTerm],
    queryFn: async () => {
      if (!debouncedSearchTerm) return [];
      const response = await get(`/users?search=${debouncedSearchTerm}`);
      return response.json();
    },
    enabled: debouncedSearchTerm.length > 0,
  });

  useEffect(() => {
    if (searchError) {
      toast({ title: 'Error', description: 'Failed to search users', variant: 'destructive' });
      console.error('Error searching users:', searchError);
    }
  }, [searchError, toast]);

  return (
    <div className="flex items-center gap-2">
      <Popover open={isUserSearchOpen} onOpenChange={setIsUserSearchOpen}>
        <PopoverTrigger asChild>
          <Button variant="outline" className="w-[250px] justify-between">
            {selectedUser ? selectedUser.email : 'Filter by owner...'}
            <Search className="ml-2 h-4 w-4 shrink-0 opacity-50" />
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-[250px] p-0">
          <Command>
            <CommandInput
              placeholder="Search users..."
              value={searchTerm}
              onValueChange={setSearchTerm}
            />
            <CommandList>
              {!searchTerm ? (
                <div className="py-6 text-center text-sm text-muted-foreground">
                  Start typing to search users...
                </div>
              ) : isSearching ? (
                <div className="py-6 text-center text-sm">
                  <Loader2 className="mx-auto h-4 w-4 animate-spin" />
                  <p className="mt-2">Searching users...</p>
                </div>
              ) : users.length === 0 ? (
                <CommandEmpty>No users found.</CommandEmpty>
              ) : (
                <CommandGroup>
                  {users.map((user) => (
                    <CommandItem
                      key={user.id}
                      onSelect={() => {
                        onUserSelect(user);
                        setIsUserSearchOpen(false);
                        setSearchTerm('');
                      }}
                    >
                      {user.email}
                    </CommandItem>
                  ))}
                </CommandGroup>
              )}
            </CommandList>
          </Command>
        </PopoverContent>
      </Popover>
      {selectedUser && (
        <Button
          variant="ghost"
          onClick={() => {
            onUserSelect(null);
            setSearchTerm('');
          }}
        >
          Clear filter
        </Button>
      )}
    </div>
  );
}
