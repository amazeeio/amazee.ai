export interface LogEntry {
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

export interface AuditLogFilters {
  skip?: number;
  limit?: number;
  event_type?: string[];
  resource_type?: string[];
  user_email?: string;
  from_date?: string;
  to_date?: string;
  status_code?: string[];
}

export interface AuditLogMetadata {
  event_types: string[];
  resource_types: string[];
  status_codes: string[];
}
