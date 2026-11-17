// Data types used by the dashboard. Their shape MIRRORS the backend API responses
// (backend/app/schemas/inventory.py), so change both together when the contract changes.
// Used by lib/api.ts, hooks/*, and all components.

// Stock status per store. The central warehouse has no threshold, so its status is null.
export type StockStatus = "OK" | "LOW" | "OUT";

// Stock of one variant at one location.
export interface StockCell {
  location_code: string;
  available: number; // sellable stock
  damaged: number; // damaged stock, never included in sales
  status: StockStatus | null;
}

export interface InventoryItem {
  variant_id: number;
  sku: string;
  product_name: string;
  variant_name: string;
  threshold: number;
  stock: StockCell[];
}

export interface InventoryResponse {
  as_of: string; // time the data was read (ISO); displayed as "Last updated"
  items: InventoryItem[];
}

// One row of stock movement history for the detail drawer.
export interface Movement {
  id: number;
  location_code: string;
  bucket: string;
  type: string;
  qty_delta: number;
  ref_type: string | null;
  ref_id: number | null;
  created_at: string;
}

// Dashboard filters; every field is optional (empty = not filtered).
export interface InventoryFilters {
  store?: string;
  q?: string;
  status?: StockStatus;
}

// Order of the location columns in the table: central warehouse first, then stores.
export const LOCATION_ORDER = ["WH", "SHOP1", "SHOP2"] as const;

export const LOCATION_LABEL: Record<string, string> = {
  WH: "Main Warehouse",
  SHOP1: "Store 1",
  SHOP2: "Store 2",
};

// ---------------- sales (POST /api/sales) ----------------

export type SaleChannel = "POS" | "ONLINE";

// Store codes selectable for a POS sale (the central warehouse is used automatically by the ONLINE channel).
export const STORE_CODES = ["SHOP1", "SHOP2"] as const;

export interface SaleBody {
  request_id: string; // idempotency key: one sale intent = one key
  variant_id: number;
  quantity: number;
  channel: SaleChannel;
  store_id?: string; // required for POS, ignored for ONLINE
}

export interface SaleResponse {
  id: number;
  request_id: string;
  variant_id: number;
  location_code: string; // location actually deducted
  quantity: number;
  channel: SaleChannel;
  remaining: number;
  odoo_status: string; // PENDING | SYNCED | FAILED
  replayed: boolean; // true if the same request_id is answered again
}

// ---------------- replenishment ----------------

export interface ReplenishmentItem {
  store_code: string;
  variant_id: number;
  sku: string;
  variant_name: string;
  available: number;
  threshold: number;
  status: StockStatus | null;
  warehouse_available: number;
  suggested_quantity: number;
  pending_request: boolean;
}

export interface ReplenishmentBody {
  request_id: string;
  store_id: string;
  variant_id: number;
  quantity: number;
}

export interface ReplenishmentResponse {
  id: number;
  request_id: string;
  store_id: string;
  variant_id: number;
  quantity: number;
  status: string; // PENDING | SUBMITTED | FAILED
  replayed: boolean;
}

// ---------------- authentication and Odoo connection status ----------------

export interface AuthUser {
  username: string;
  expires_at?: string | null; // when the session expires (only from GET /api/auth/me)
}

export type ConnectionState = "connected" | "disconnected" | "unknown";

// Shape of the GET /api/odoo/connection response. Deliberately no URL, login, or API key:
// credentials exist only in the backend and are never sent to the browser.
export interface OdooConnection {
  mode: "mock" | "live";
  status: ConnectionState;
  database: string | null;
  odoo_version: string | null;
  last_checked_at: string | null;
  error: { code: string; message: string } | null;
}

// Odoo configuration stored in the backend. The API key is NEVER here: only the api_key_set indicator.
// source "none" = live mode without configuration (not yet connected); "mock" = built-in sample data.
export interface OdooConfig {
  source: "database" | "mock" | "none";
  configured: boolean;
  url: string | null;
  database: string | null;
  api_key_set: boolean;
}

// Configuration form content: only URL, database name, and API key.
// reset_local_data: agrees to delete local data belonging to the previous Odoo when switching to another Odoo.
export interface OdooConfigInput {
  url: string;
  database: string;
  api_key: string;
  reset_local_data?: boolean;
}
