export interface PrivateAIKey {
  id: number;
  name: string;
  database_name: string;
  database_host: string;
  database_username: string;
  database_password: string;
  region: string;
  created_at: string;
  owner_id: number;
  litellm_token?: string;
  litellm_api_url?: string;
}