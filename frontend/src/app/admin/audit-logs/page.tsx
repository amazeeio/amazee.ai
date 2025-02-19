'use client';

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { format, parseISO } from 'date-fns';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useToast } from '@/hooks/use-toast';
import { Loader2, Info } from 'lucide-react';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';

interface LogEntry {
  id: string;
  timestamp: string;
  action: string;
  details: Record<string, unknown>;
  user_id: string;
  event_type: string;
  resource_type: string;
  user_email: string;
  request_source: string;
  ip_address: string;
}

interface AuditLogFilters {
  skip?: number;
  limit?: number;
  event_type?: string;
  resource_type?: string;
  user_email?: string;
  from_date?: string;
  to_date?: string;
}

const ITEMS_PER_PAGE = 20;

export default function AuditLogsPage() {
  const { toast } = useToast();
  const [filters, setFilters] = useState<AuditLogFilters>({
    skip: 0,
    limit: ITEMS_PER_PAGE,
  });

  const { data: logs = [], isLoading } = useQuery({
    queryKey: ['audit-logs', filters],
    queryFn: async (): Promise<LogEntry[]> => {
      try {
        const queryParams = new URLSearchParams();
        Object.entries(filters).forEach(([key, value]) => {
          if (value) queryParams.append(key, value.toString());
        });

        const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/audit/logs?${queryParams}`, {
          credentials: 'include',
        });
        if (!response.ok) {
          const error = await response.json();
          throw new Error(error.detail || 'Failed to fetch audit logs');
        }
        const data = await response.json();
        return data as LogEntry[];
      } catch (error) {
        const message = error instanceof Error ? error.message : 'Failed to fetch audit logs';
        toast({
          title: 'Error',
          description: message,
          variant: 'destructive',
        });
        throw error;
      }
    },
  });

  const handleFilterChange = (key: keyof AuditLogFilters, value: string | undefined) => {
    setFilters(prev => ({
      ...prev,
      [key]: value,
      skip: 0, // Reset pagination when filters change
    }));
  };

  const handleNextPage = () => {
    setFilters(prev => ({
      ...prev,
      skip: (prev.skip || 0) + ITEMS_PER_PAGE,
    }));
  };

  const handlePrevPage = () => {
    setFilters(prev => ({
      ...prev,
      skip: Math.max((prev.skip || 0) - ITEMS_PER_PAGE, 0),
    }));
  };

  const getStatusBadge = (status: number) => {
    const getStatusColor = (status: number) => {
      if (status < 300) return 'bg-green-100 text-green-800';
      if (status < 400) return 'bg-blue-100 text-blue-800';
      if (status < 500) return 'bg-orange-100 text-orange-800';
      return 'bg-red-100 text-red-800';
    };

    return (
      <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${getStatusColor(status)}`}>
        {status}
      </span>
    );
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <Loader2 className="h-8 w-8 animate-spin" />
      </div>
    );
  }

  // Get unique event types and resource types
  const uniqueEventTypes = Array.from(new Set((logs as LogEntry[]).map(log => log.event_type))).filter(Boolean) as string[];
  const uniqueResourceTypes = Array.from(new Set((logs as LogEntry[]).map(log => log.resource_type))).filter(Boolean) as string[];

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h1 className="text-3xl font-bold">Audit Logs</h1>
      </div>

      {/* Filters */}
      <Card>
        <CardHeader>
          <CardTitle>Filters</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">Event Type</label>
              <Select
                value={filters.event_type}
                onValueChange={(value) => handleFilterChange('event_type', value)}
              >
                <SelectTrigger>
                  <SelectValue placeholder="All Events" />
                </SelectTrigger>
                <SelectContent>
                  {uniqueEventTypes.map((type) => (
                    <SelectItem key={type} value={type}>{type}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-2">
              <label className="text-sm font-medium">Resource Type</label>
              <Select
                value={filters.resource_type}
                onValueChange={(value) => handleFilterChange('resource_type', value)}
              >
                <SelectTrigger>
                  <SelectValue placeholder="All Resources" />
                </SelectTrigger>
                <SelectContent>
                  {uniqueResourceTypes.map((type) => (
                    <SelectItem key={type} value={type}>{type}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-2">
              <label className="text-sm font-medium">User Email</label>
              <Input
                type="email"
                placeholder="Search by email"
                value={filters.user_email || ''}
                onChange={(e) => handleFilterChange('user_email', e.target.value)}
              />
            </div>

            <div className="space-y-2">
              <label className="text-sm font-medium">From Date</label>
              <Input
                type="datetime-local"
                value={filters.from_date || ''}
                onChange={(e) => handleFilterChange('from_date', e.target.value)}
              />
            </div>

            <div className="space-y-2">
              <label className="text-sm font-medium">To Date</label>
              <Input
                type="datetime-local"
                value={filters.to_date || ''}
                onChange={(e) => handleFilterChange('to_date', e.target.value)}
              />
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Logs Table */}
      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Timestamp</TableHead>
                <TableHead>Event</TableHead>
                <TableHead>Resource</TableHead>
                <TableHead>Action</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>User</TableHead>
                <TableHead>Source</TableHead>
                <TableHead>IP Address</TableHead>
                <TableHead>Details</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(logs as LogEntry[]).map((log: LogEntry) => (
                <TableRow key={log.id}>
                  <TableCell className="whitespace-nowrap">
                    {format(parseISO(log.timestamp), 'yyyy-MM-dd HH:mm:ss')}
                  </TableCell>
                  <TableCell>
                    <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-800">
                      {log.event_type}
                    </span>
                  </TableCell>
                  <TableCell>
                    <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-purple-100 text-purple-800">
                      {log.resource_type}
                    </span>
                  </TableCell>
                  <TableCell className="whitespace-nowrap">{log.action}</TableCell>
                  <TableCell>{getStatusBadge(log.details.status_code as number)}</TableCell>
                  <TableCell className="whitespace-nowrap">
                    {log.user_email || 'Anonymous'}
                  </TableCell>
                  <TableCell>
                    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                      log.request_source === 'frontend'
                        ? 'bg-green-100 text-green-800'
                        : log.request_source === 'api'
                        ? 'bg-yellow-100 text-yellow-800'
                        : 'bg-gray-100 text-gray-800'
                    }`}>
                      {log.request_source || 'Unknown'}
                    </span>
                  </TableCell>
                  <TableCell className="whitespace-nowrap">
                    {log.ip_address || '-'}
                  </TableCell>
                  <TableCell>
                    <TooltipProvider>
                      <Tooltip>
                        <TooltipTrigger>
                          <Info className="h-4 w-4 text-muted-foreground hover:text-foreground" />
                        </TooltipTrigger>
                        <TooltipContent>
                          <pre className="text-xs">
                            {JSON.stringify(log.details, null, 2)}
                          </pre>
                        </TooltipContent>
                      </Tooltip>
                    </TooltipProvider>
                  </TableCell>
                </TableRow>
              ))}
              {logs.length === 0 && (
                <TableRow>
                  <TableCell colSpan={9} className="text-center py-4">
                    No audit logs found
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* Pagination */}
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm text-muted-foreground">
            Page {Math.floor((filters.skip || 0) / ITEMS_PER_PAGE) + 1}
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            onClick={handlePrevPage}
            disabled={!filters.skip || filters.skip === 0}
          >
            Previous
          </Button>
          <Button
            variant="outline"
            onClick={handleNextPage}
            disabled={(logs as LogEntry[]).length < ITEMS_PER_PAGE}
          >
            Next
          </Button>
        </div>
      </div>
    </div>
  );
}