export interface PricingTable {
  pricing_table_id: string;
  updated_at: string;
  stripe_publishable_key: string;
}

export interface PricingTables {
  tables: Record<string, PricingTable | null>;
}
