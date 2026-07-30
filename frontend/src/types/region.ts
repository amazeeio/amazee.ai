export interface Region {
  id: number;
  name: string;
  label: string;
  description: string;
  postgres_host: string;
  postgres_port: number;
  postgres_admin_user: string;
  postgres_admin_password?: string;
  litellm_api_url: string;
  litellm_api_key?: string;
  is_active: boolean;
  is_dedicated: boolean;
  // Which market the region serves (US, US+CA, EU, DE, CH, UK, AU, APAC, GLOBAL)
  regional_area?: string | null;
  // null = access-group enforcement off (legacy all-models behavior)
  default_access_group_id?: number | null;
}

export const REGIONAL_AREAS = [
  "US",
  "US+CA",
  "EU",
  "DE",
  "CH",
  "UK",
  "AU",
  "APAC",
  "GLOBAL",
] as const;
