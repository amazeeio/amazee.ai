import { useMemo } from "react";
import { AuditLogMetadata } from "@/types/audit-log";

export interface AuditLogMetadataOptions {
  eventTypes: { value: string; label: string }[];
  resourceTypes: { value: string; label: string }[];
  statusCodes: { value: string; label: string }[];
}

const getStatusCodeDescription = (code: number): string => {
  const descriptions: Record<number, string> = {
    200: "OK",
    201: "Created",
    204: "No Content",
    301: "Moved Permanently",
    302: "Found",
    304: "Not Modified",
    307: "Temporary Redirect",
    308: "Permanent Redirect",
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    422: "Unprocessable Entity",
    429: "Too Many Requests",
    500: "Internal Server Error",
    502: "Bad Gateway",
    503: "Service Unavailable",
    504: "Gateway Timeout",
  };
  return descriptions[code] || "Unknown";
};

export function useAuditLogMetadata(
  metadata: AuditLogMetadata | undefined,
): AuditLogMetadataOptions {
  return useMemo(() => {
    if (!metadata)
      return { eventTypes: [], resourceTypes: [], statusCodes: [] };
    return {
      eventTypes: (metadata.event_types || []).map((t) => ({
        value: t,
        label: t.charAt(0).toUpperCase() + t.slice(1).toLowerCase(),
      })),
      resourceTypes: (metadata.resource_types || []).map((t) => ({
        value: t,
        label: t.charAt(0).toUpperCase() + t.slice(1).toLowerCase(),
      })),
      statusCodes: (metadata.status_codes || []).map((c) => ({
        value: c,
        label: `${c} - ${getStatusCodeDescription(parseInt(c))}`,
      })),
    };
  }, [metadata]);
}
