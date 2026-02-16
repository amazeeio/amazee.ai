'use client';

import { useState, useMemo, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { useToast } from '@/hooks/use-toast';
import { Loader2 } from 'lucide-react';
import { get } from '@/utils/api';
import { AuditLogFilters as IFilters, LogEntry, AuditLogMetadata } from '@/types/audit-log';
import { useAuditLogMetadata } from '@/hooks/use-audit-log-metadata';
import { AuditLogFilters } from './_components/audit-log-filters';
import { AuditLogTable } from './_components/audit-log-table';

const ITEMS_PER_PAGE = 20;

export default function AuditLogsPage() {
  const { toast } = useToast();
  const [currentPage, setCurrentPage] = useState<number>(1);
  const [filters, setFilters] = useState<IFilters>({
    event_type: [],
    resource_type: [],
    status_code: [],
    user_email: '',
  });

  const { data: metadata, isLoading: isLoadingMetadata, error: metadataError } = useQuery<AuditLogMetadata>({
    queryKey: ['audit-logs-metadata'],
    queryFn: async () => {
      const response = await get('audit/logs/metadata');
      return response.json();
    },
  });

  const queryParams = useMemo(() => {
    const params = new URLSearchParams({
      skip: ((currentPage - 1) * ITEMS_PER_PAGE).toString(),
      limit: ITEMS_PER_PAGE.toString(),
    });
    if (filters.event_type?.length) params.append('event_type', filters.event_type.join(','));
    if (filters.resource_type?.length) params.append('resource_type', filters.resource_type.join(','));
    if (filters.status_code?.length) params.append('status_code', filters.status_code.join(','));
    if (filters.user_email) params.append('user_email', filters.user_email);
    return params.toString();
  }, [currentPage, filters]);

  const { data: logsData, isLoading: isLoadingLogs, error: logsError } = useQuery<{ items: LogEntry[]; total: number }>({
    queryKey: ['audit-logs', queryParams],
    queryFn: async () => {
      const response = await get(`audit/logs?${queryParams}`);
      return response.json();
    },
  });

  useEffect(() => {
    if (metadataError) {
      toast({ title: 'Error', description: 'Failed to fetch audit logs metadata', variant: 'destructive' });
      console.error('Error fetching audit logs metadata:', metadataError);
    }
  }, [metadataError, toast]);

  useEffect(() => {
    if (logsError) {
      toast({ title: 'Error', description: 'Failed to fetch audit logs', variant: 'destructive' });
      console.error('Error fetching audit logs:', logsError);
    }
  }, [logsError, toast]);

  const handleFilterChange = (key: keyof IFilters, value: string[] | string | undefined) => {
    setFilters(prev => ({ ...prev, [key]: value }));
    setCurrentPage(1);
  };

  const metadataOptions = useAuditLogMetadata(metadata);

  if (isLoadingMetadata || (isLoadingLogs && !logsData)) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <Loader2 className="h-8 w-8 animate-spin" />
      </div>
    );
  }

  const totalItems = logsData?.total || 0;
  const totalPages = Math.ceil(totalItems / ITEMS_PER_PAGE);

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h1 className="text-3xl font-bold">Audit Logs</h1>
      </div>

      <AuditLogFilters 
        filters={filters} 
        onFilterChange={handleFilterChange} 
        metadata={metadataOptions} 
      />

      <AuditLogTable logs={logsData?.items || []} />

      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          Page {currentPage} of {totalPages || 1} ({totalItems} total)
        </p>
        <div className="flex gap-2">
          <Button
            variant="outline"
            onClick={() => setCurrentPage(prev => Math.max(prev - 1, 1))}
            disabled={currentPage === 1}
          >
            Previous
          </Button>
          <Button
            variant="outline"
            onClick={() => setCurrentPage(prev => prev + 1)}
            disabled={currentPage >= totalPages}
          >
            Next
          </Button>
        </div>
      </div>
    </div>
  );
}
