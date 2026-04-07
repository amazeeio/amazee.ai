export interface User {
  id: string | number;
  email: string;
  is_active: boolean;
  is_admin?: boolean;
  role?: string;
  created_at?: string;
  team_name?: string;
  team_id?: number | null;
}

export const USER_ROLES = [
  { value: "admin", label: "Admin" },
  { value: "key_creator", label: "Key Creator" },
  { value: "read_only", label: "Read Only" },
  { value: "sales", label: "Sales" },
];
