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
}
