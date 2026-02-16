import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { MultiSelect } from '@/components/ui/multi-select';
import { AuditLogFilters as IFilters } from '@/types/audit-log';
import { AuditLogMetadataOptions } from '@/hooks/use-audit-log-metadata';

interface AuditLogFiltersProps {
  filters: IFilters;
  onFilterChange: (key: keyof IFilters, value: string[] | string | undefined) => void;
  metadata: AuditLogMetadataOptions;
}

export function AuditLogFilters({ filters, onFilterChange, metadata }: AuditLogFiltersProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Filters</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="space-y-2">
            <label className="text-sm font-medium">Event Type</label>
            <MultiSelect
              options={metadata.eventTypes}
              onValueChange={(value) => onFilterChange('event_type', value)}
              defaultValue={filters.event_type || []}
              placeholder="Select Events"
            />
          </div>

          <div className="space-y-2">
            <label className="text-sm font-medium">Resource Type</label>
            <MultiSelect
              options={metadata.resourceTypes}
              onValueChange={(value) => onFilterChange('resource_type', value)}
              defaultValue={filters.resource_type || []}
              placeholder="Select Resources"
            />
          </div>

          <div className="space-y-2">
            <label className="text-sm font-medium">Status Code</label>
            <MultiSelect
              options={metadata.statusCodes}
              onValueChange={(value) => onFilterChange('status_code', value)}
              defaultValue={filters.status_code || []}
              placeholder="Select Status Codes"
            />
          </div>

          <div className="space-y-2">
            <label className="text-sm font-medium">User Email</label>
            <Input
              type="email"
              placeholder="Search by email"
              value={filters.user_email || ''}
              onChange={(e) => onFilterChange('user_email', e.target.value)}
            />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
