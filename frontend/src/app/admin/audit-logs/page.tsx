'use client';

import { useState, useEffect, useCallback } from 'react';
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
import { get } from '@/utils/api';
import { MultiSelect } from '@/components/ui/multi-select';

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
  event_type?: string[];
  resource_type?: string[];
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
  const [auditLogs, setAuditLogs] = useState<LogEntry[]>([]);
  const [totalItems, setTotalItems] = useState<number>(0);
  const [currentPage, setCurrentPage] = useState<number>(1);
  const [itemsPerPage] = useState<number>(ITEMS_PER_PAGE);
  const [startDate] = useState<string | null>(null);
  const [endDate] = useState<string | null>(null);
  const [selectedUser] = useState<string | null>(null);
  const [selectedAction] = useState<string | null>(null);
  const [eventTypeOptions, setEventTypeOptions] = useState<{ value: string; label: string }[]>([]);
  const [resourceTypeOptions, setResourceTypeOptions] = useState<{ value: string; label: string }[]>([]);

  const fetchMetadata = useCallback(async () => {
    try {
      const response = await get('audit/logs/metadata', { credentials: 'include' });
      const data = await response.json();

      // Convert string arrays to option objects
      const eventOptions = (data.event_types || [])
        .filter(Boolean)
        .map((type: string) => ({
          value: type,
          label: type.charAt(0).toUpperCase() + type.slice(1).toLowerCase(),
        }));

      setEventTypeOptions(eventOptions);
      console.log('Event Type Options:', eventOptions);

      setResourceTypeOptions(
        (data.resource_types || [])
          .filter(Boolean)
          .map((type: string) => ({
            value: type,
            label: type.charAt(0).toUpperCase() + type.slice(1).toLowerCase(),
          }))
      );
    } catch (error) {
      console.error('Error fetching audit logs metadata:', error);
      toast({
        title: 'Error',
        description: 'Failed to fetch audit logs metadata',
        variant: 'destructive',
      });
    }
  }, [toast]);

  useEffect(() => {
    void fetchMetadata();
  }, [fetchMetadata]);

  const fetchAuditLogs = useCallback(async () => {
    try {
      const queryParams = new URLSearchParams({
        skip: ((currentPage - 1) * itemsPerPage).toString(),
        limit: itemsPerPage.toString(),
        ...(filters.event_type?.length && { event_type: filters.event_type.join(',') }),
        ...(filters.resource_type?.length && { resource_type: filters.resource_type.join(',') }),
        ...(filters.user_email && { user_email: filters.user_email }),
        ...(filters.from_date && { from_date: filters.from_date }),
        ...(filters.to_date && { to_date: filters.to_date }),
      }).toString();

      const response = await get(`audit/logs?${queryParams}`, { credentials: 'include' });
      const data = await response.json();
      setAuditLogs(data.items || []);
      setTotalItems(data.total || 0);
    } catch (error) {
      console.error('Error fetching audit logs:', error);
      toast({
        title: 'Error',
        description: 'Failed to fetch audit logs',
        variant: 'destructive',
      });
    }
  }, [currentPage, itemsPerPage, filters, toast]);

  useEffect(() => {
    void fetchAuditLogs();
  }, [currentPage, itemsPerPage, filters, fetchAuditLogs]);

  const handleFilterChange = (key: keyof AuditLogFilters, value: string[] | string | undefined) => {
    setFilters(prev => ({
      ...prev,
      [key]: value,
      skip: 0, // Reset pagination when filters change
    }));
    setCurrentPage(1); // Reset to first page when filters change
  };

  const handleNextPage = () => {
    setCurrentPage(prev => prev + 1);
  };

  const handlePrevPage = () => {
    setCurrentPage(prev => Math.max(prev - 1, 1));
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

  if (!auditLogs.length) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <Loader2 className="h-8 w-8 animate-spin" />
      </div>
    );
  }

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
              <MultiSelect
                options={eventTypeOptions}
                onValueChange={(value) => handleFilterChange('event_type', value)}
                defaultValue={filters.event_type || []}
                placeholder="Select Events"
              />
            </div>

            <div className="space-y-2">
              <label className="text-sm font-medium">Resource Type</label>
              <MultiSelect
                options={resourceTypeOptions}
                onValueChange={(value) => handleFilterChange('resource_type', value)}
                defaultValue={filters.resource_type || []}
                placeholder="Select Resources"
              />
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
              {auditLogs.map((log: LogEntry) => (
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
              {auditLogs.length === 0 && (
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
            Page {currentPage} of {Math.ceil(totalItems / itemsPerPage)}
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            onClick={handlePrevPage}
            disabled={currentPage === 1}
          >
            Previous
          </Button>
          <Button
            variant="outline"
            onClick={handleNextPage}
            disabled={currentPage === Math.ceil(totalItems / itemsPerPage)}
          >
            Next
          </Button>
        </div>
      </div>
    </div>
  );
}