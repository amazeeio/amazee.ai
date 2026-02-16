export interface Product {
  id: string;
  name: string;
  user_count: number;
  keys_per_user: number;
  total_key_count: number;
  service_key_count: number;
  max_budget_per_key: number;
  rpm_per_key: number;
  vector_db_count: number;
  vector_db_storage: number;
  renewal_period_days: number;
  active: boolean;
  created_at: string;
}
