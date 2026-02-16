import { Product } from "./product";
import { User } from "./user";

export interface TeamUser extends User {
  id: number;
}

export interface Team {
  id: string;
  name: string;
  admin_email: string;
  phone: string;
  billing_address: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  last_payment?: string;
  users?: TeamUser[];
  products?: Product[];
  is_always_free: boolean;
  force_user_keys?: boolean;
  deleted_at?: string;
  retention_warning_sent_at?: string;
}
