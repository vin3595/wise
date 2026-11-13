export type Role = "owner" | "manager" | "cashier" | "captain" | "kitchen";

export interface Brand {
  id: string;
  name: string;
  logo_url: string | null;
  primary_color: string | null;
  is_active: boolean;
}

export interface Tenant {
  id: string;
  login_id: string;
  name: string;
  app_name: string | null;
  logo_url: string | null;
  primary_color: string;
  ui_theme?: UiTheme;
  custom_domain: string | null;
  show_powered_by: boolean;
  gstin: string | null;
  plan: string;
  status: string;
  trial_ends_at: string | null;
}

export interface PublicBranding {
  login_id: string;
  name: string;
  app_name: string;
  logo_url: string | null;
  primary_color: string;
  ui_theme?: UiTheme;
  show_powered_by: boolean;
  brands: Brand[];
}

export interface User {
  id: string;
  name: string;
  role: Role;
  email: string | null;
  phone: string | null;
  is_active: boolean;
  has_pin: boolean;
  is_screen?: boolean;
  outlet_ids: string[];
}

export interface TokenOut {
  token: string;
  user: User;
  tenant: Tenant;
  outlet_id: string | null;
}

export interface Me {
  tenant: Tenant;
  user: User;
  outlet_id: string | null;
  training?: boolean;       // signed in to a training outlet
  permissions: string[];
}

export interface Outlet {
  id: string;
  brand_id: string;
  name: string;
  code: string;
  gstin: string | null;
  fssai: string | null;
  address: string | null;
  state_code: string | null;
  phone: string | null;
  bill_footer: string | null;
  car_number_required: boolean;
  order_types: OrderType[];
  default_order_type: OrderType;
  stock_check_frequency_days: number;
  is_active: boolean;
  display_key: string;
  kitchen_key: string;
  upi_vpa: string | null;
  upi_payee_name: string | null;
  qr_ordering: boolean;
  qr_auto_accept: boolean;
  kiosk_enabled: boolean;
  print_kots?: boolean;
  print_key?: string;
  table_labels: string[] | null;
  qr_key: string;
  kitchen_key?: string;
  is_training?: boolean;    // practice copy: nothing there is real
  training_of?: string | null;
}

export interface TaxGroup {
  id: string;
  name: string;
  cgst_bp: number;
  sgst_bp: number;
  igst_bp: number;
  is_inclusive: boolean;
  is_default: boolean;
  is_active: boolean;
}

export interface Category {
  id: string;
  brand_id: string;
  name: string;
  parent_id: string | null;
  color: string | null;
  sort_order: number;
  is_active: boolean;
}

export interface Variant {
  id?: string;
  name: string;
  sku: string | null;
  price_paise: number;
  sort_order: number;
  is_active: boolean;
}

export type FoodType = "veg" | "non_veg" | "egg";

export interface Item {
  id: string;
  brand_id: string;
  category_id: string;
  name: string;
  short_code: string | null;
  food_type: FoodType;
  hsn_sac: string | null;
  tax_group_id: string | null;
  prep_area_id: string | null;
  description: string | null;
  image_url: string | null;
  sort_order: number;
  is_active: boolean;
  variants: Variant[];
  addon_group_ids: string[];
}

export interface Addon {
  id?: string;
  name: string;
  price_paise: number;
  is_active: boolean;
}

export interface AddonGroup {
  id: string;
  brand_id: string;
  name: string;
  min_select: number;
  max_select: number;
  is_active: boolean;
  addons: Addon[];
}

export interface PrepArea {
  id: string;
  name: string;
  sort_order: number;
  is_active: boolean;
  printer_host?: string | null;
  printer_port?: number;
  paper_mm?: number;
  kot_copies?: number;
  station_seen_at?: string | null;
  last_print_ok_at?: string | null;
  last_print_error?: string | null;
  last_print_error_at?: string | null;
}

export interface PrintJob {
  kot_id: string;
  kind: "kot" | "reprint" | "cancel";
  prep_area_id: string | null;
  printer: { host: string | null; port: number; paper_mm: number; copies: number } | null;
  printed_before: number;
  taken_by: string | null;
  kot: Kot;
}

export interface PrintFeed {
  outlet_name: string;
  app_name: string;
  enabled: boolean;
  areas: PrepArea[];
  jobs: PrintJob[];
  server_time: string;
}

export interface BrandMenu {
  brand_id: string;
  categories: Category[];
  items: Item[];
  addon_groups: AddonGroup[];
  tax_groups: TaxGroup[];
  prep_areas: PrepArea[];
}

export interface OutletMenuVariant {
  variant_id: string;
  name: string;
  master_price_paise: number;
  price_paise: number;
  available: boolean;
  overridden: boolean;
  sold_out?: boolean;     // for the rest of today (tapped at the counter or in the kitchen)
}

export interface OutletMenu {
  outlet_id: string;
  brand_id: string;
  categories: Category[];
  items: OutletMenuItem[];
}

export interface OutletMenuItem {
  item_id: string;
  category_id: string;
  name: string;
  short_code: string | null;
  food_type: FoodType;
  tax_group_id: string | null;
  prep_area_id: string | null;
  image_url?: string | null;
  description?: string | null;
  variants: OutletMenuVariant[];
  addon_groups: AddonGroup[];
}

export type OrderType = "dine_in" | "takeaway" | "delivery";
export type KotStatus = "new" | "preparing" | "ready" | "served";
export type OrderStatus = "pending" | "new" | "preparing" | "ready" | "completed" | "cancelled";

export interface KotLine {
  id: string;
  item_name: string;
  variant_name: string;
  food_type: FoodType;
  qty: number;
  addons: { id: string; name: string; price_paise: number }[];
  notes: string | null;
}

export interface Kot {
  id: string;
  order_id: string;
  kot_no: number;
  prep_area_id: string | null;
  prep_area_name: string;
  status: KotStatus;
  created_at: string;
  started_at: string | null;
  ready_at: string | null;
  age_level: "ok" | "warn" | "late";
  token_no: number;
  order_type: OrderType;
  table_label: string | null;
  car_number: string | null;
  customer_name: string | null;
  order_notes: string | null;
  is_addon_kot: boolean;
  printed?: boolean;
  lines: KotLine[];
}

export interface Order {
  id: string;
  outlet_id: string;
  token_no: number | null;
  source: "staff" | "qr" | "kiosk";
  customer_phone: string | null;
  pending_lines: { item: string; variant: string; qty: number; addons: string[]; notes: string | null }[];
  merged_into_existing: boolean;
  payment_status: "awaiting" | "paid" | null;
  pay_method: "upi" | "counter" | null;
  quoted_total_paise: number | null;
  pay_ref: string | null;
  bill_id: string | null;
  invoice_no: string | null;
  cancel_reason: string | null;
  order_type: OrderType;
  table_label: string | null;
  car_number: string | null;
  customer_name: string | null;
  notes: string | null;
  status: OrderStatus;
  created_at: string;
  ready_at: string | null;
  completed_at: string | null;
  kots: Kot[];
  total_paise: number;
}

export interface DisplayOrder {
  token_no: number;
  order_type: OrderType;
  car_number: string | null;
  table_label: string | null;
}

export interface Display {
  outlet_name: string;
  app_name: string;
  logo_url: string | null;
  primary_color: string;
  show_powered_by: boolean;
  preparing: DisplayOrder[];
  ready: DisplayOrder[];
  server_time: string;
}

export interface ImportIssue {
  sheet: string;
  row: number;
  message: string;
}

export interface ImportResult {
  dry_run: boolean;
  ok: boolean;
  summary: Record<string, number>;
  errors: ImportIssue[];
  warnings: ImportIssue[];
  created: { created: Record<string, number>; updated: Record<string, number> } | null;
}

export type PayMode = "cash" | "upi" | "card" | "due" | "complimentary";

export interface BillLine {
  id?: string | null;
  item_name: string;
  variant_name: string;
  addons_text: string | null;
  hsn_sac: string | null;
  qty: number;
  unit_paise: number;
  gross_paise: number;
  discount_paise: number;
  taxable_paise: number;
  cgst_paise: number;
  sgst_paise: number;
  igst_paise: number;
  rate_bp: number;
  tax_inclusive: boolean;
}

export interface TaxRow { rate_bp: number; taxable_paise: number; cgst_paise: number; sgst_paise: number; igst_paise: number }

export interface BillPreview {
  lines: BillLine[];
  gross_paise: number;
  discount_paise: number;
  taxable_paise: number;
  cgst_paise: number;
  sgst_paise: number;
  igst_paise: number;
  round_off_paise: number;
  total_paise: number;
  tax_summary: TaxRow[];
  needs_approval: string[];
}

export interface Bill extends BillPreview {
  training?: boolean;
  id: string;
  invoice_no: string;
  status: "paid" | "cancelled";
  order_id: string;
  token_no: number | null;
  order_type: OrderType;
  table_label: string | null;
  car_number: string | null;
  business_date: string;
  created_at: string;
  customer_name: string | null;
  customer_phone: string | null;
  customer_gstin: string | null;
  discount_reason: string | null;
  payments: { mode: PayMode; amount_paise: number; reference: string | null }[];
  cashier_name: string | null;
  cancel_reason: string | null;
  cancelled_at: string | null;
  outlet: { name: string; legal_name: string; address: string | null; gstin: string | null; fssai: string | null;
    phone: string | null; footer: string | null; logo_url: string | null; brand: string };
  upi_link: string | null;
  revision?: number;
  edited_at?: string | null;
  locked?: boolean;
}

export interface BillFixResult {
  bill_id: string;
  invoice_no: string;
  ok: boolean;
  error: string | null;
  preview: BillPreview | null;
  changes: string[];
  needs_approval: string[];
  old_total_paise: number;
  new_total_paise: number;
}

export interface BillRevision {
  revision_no: number;
  created_at: string;
  edited_by_name: string | null;
  approved_by_name: string | null;
  reason: string;
  changes: string[];
  old_total_paise: number;
  new_total_paise: number;
  before: Bill;
}

export interface Shift {
  id: string;
  outlet_id: string;
  status: "open" | "closed";
  opened_at: string;
  opened_by_name: string | null;
  opening_cash_paise: number;
  movements?: { kind: "in" | "out"; amount_paise: number; denoms: Record<string, number> | null; reason: string; at: string | null; by?: string | null;
    purpose?: CashPurpose | null; category?: string | null; handed?: "owner" | "bank" | null; paid_to?: string | null }[];
  drawer_id?: string | null;
  drawer_name?: string | null;
  closed_by_name?: string | null;
  opening_denoms?: Record<string, number> | null;
  counted_denoms?: Record<string, number> | null;
  closed_at: string | null;
  cash_sales_paise: number | null;
  cash_in_paise: number;
  cash_out_paise: number;
  expected_cash_paise: number | null;
  counted_cash_paise: number | null;
  variance_paise: number | null;
  bills: number;
  sales_paise: number;
  by_mode: Record<string, number>;
  expected_opening_paise?: number | null;
  manager_alerted?: boolean;
  opening_difference_paise?: number | null;
}

export interface SelfOrderStatus {
  order_id: string;
  status: OrderStatus;
  token_no: number | null;
  table: string | null;
  message: string | null;
  total_paise: number;
  payment_status: "awaiting" | "paid" | null;
  pay_ref: string | null;
  upi_link: string | null;
}

export type UiTheme = "steel" | "board" | "irani" | "swiss" | "shake";

/** What cash taken out of / put into a drawer was for. */
export type CashPurpose = "expense" | "drawing" | "supplier" | "topup" | "change" | "other";
