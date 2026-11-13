"""Request/response models. Money crosses the API as integer paise."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .core.contact import normalize_email, normalize_phone, validate_gstin, validate_hex_color
from .core.login_id import validate_login_id
from .core.permissions import Role
from .core.security import validate_pin


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------------ auth

class SignupStart(BaseModel):
    login_id: str
    restaurant_name: str = Field(min_length=2, max_length=120)
    owner_name: str = Field(min_length=2, max_length=120)
    email: str
    phone: str

    _v_login = field_validator("login_id")(lambda v: validate_login_id(v))
    _v_email = field_validator("email")(lambda v: normalize_email(v))
    _v_phone = field_validator("phone")(lambda v: normalize_phone(v))


class SignupVerify(SignupStart):
    otp: str = Field(min_length=6, max_length=6)


class OtpRequest(BaseModel):
    login_id: str
    identifier: str  # email or mobile


class OtpVerify(OtpRequest):
    otp: str = Field(min_length=6, max_length=6)


class PinLogin(BaseModel):
    login_id: str
    outlet_id: uuid.UUID
    pin: str = Field(min_length=4, max_length=4)


class OtpSent(BaseModel):
    sent_to: str
    dev_otp: str | None = None  # only returned when WISE_OTP_PROVIDER=console (local development)


class TokenOut(BaseModel):
    token: str
    user: "UserOut"
    tenant: "TenantOut"
    outlet_id: uuid.UUID | None = None


# ------------------------------------------------------------------ tenant / branding

class BrandOut(ORM):
    id: uuid.UUID
    name: str
    logo_url: str | None
    primary_color: str | None
    is_active: bool


class TenantOut(ORM):
    id: uuid.UUID
    login_id: str
    name: str
    app_name: str | None
    logo_url: str | None
    primary_color: str
    ui_theme: str = "steel"
    custom_domain: str | None
    show_powered_by: bool
    gstin: str | None
    plan: str
    status: str
    trial_ends_at: datetime | None


class PublicBranding(BaseModel):
    login_id: str
    name: str
    app_name: str
    logo_url: str | None
    primary_color: str
    ui_theme: str = "steel"
    show_powered_by: bool
    brands: list[BrandOut]


class BrandingUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    app_name: str | None = Field(default=None, max_length=60)
    primary_color: str | None = None
    gstin: str | None = None
    ui_theme: str | None = Field(default=None, pattern=r"^(steel|board|irani|swiss|shake)$")

    _v_color = field_validator("primary_color")(lambda v: v if v is None else validate_hex_color(v))
    _v_gstin = field_validator("gstin")(lambda v: validate_gstin(v))


class BrandIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    primary_color: str | None = None
    is_active: bool = True

    _v_color = field_validator("primary_color")(lambda v: v if v is None else validate_hex_color(v))


class OutletIn(BaseModel):
    brand_id: uuid.UUID
    name: str = Field(min_length=1, max_length=120)
    code: str = Field(min_length=2, max_length=4, pattern=r"^[A-Za-z0-9]+$")
    gstin: str | None = None
    fssai: str | None = Field(default=None, max_length=14)
    address: str | None = None
    state_code: str | None = Field(default=None, pattern=r"^[0-9]{2}$")
    phone: str | None = None
    bill_footer: str | None = Field(default=None, max_length=300)
    car_number_required: bool = False
    order_types: list[str] = Field(default=["dine_in", "takeaway", "delivery"], min_length=1)
    default_order_type: str = Field(default="takeaway", pattern=r"^(dine_in|takeaway|delivery)$")
    stock_check_frequency_days: int = Field(default=7, ge=1, le=90)
    upi_vpa: str | None = Field(default=None, max_length=80, pattern=r"^[A-Za-z0-9.\-_]{2,}@[A-Za-z]{2,}$")
    upi_payee_name: str | None = Field(default=None, max_length=60)
    qr_ordering: bool = False
    qr_auto_accept: bool = False
    kiosk_enabled: bool = False
    print_kots: bool = False
    table_labels: list[str] = Field(default=[], max_length=200)
    is_active: bool = True

    _v_tables = field_validator("table_labels")(
        lambda v: list(dict.fromkeys(t.strip().upper()[:20] for t in v if t and t.strip())))

    _v_code = field_validator("code")(lambda v: v.upper())
    _v_gstin = field_validator("gstin")(lambda v: validate_gstin(v))
    _v_phone = field_validator("phone")(lambda v: v if not v else normalize_phone(v))

    @field_validator("order_types")
    @classmethod
    def _types(cls, v: list[str]) -> list[str]:
        allowed = ["dine_in", "takeaway", "delivery"]
        bad = [t for t in v if t not in allowed]
        if bad:
            raise ValueError(f"Unknown order type: {bad[0]}")
        return [t for t in allowed if t in v]

    @model_validator(mode="after")
    def _default_is_on(self) -> "OutletIn":
        if self.default_order_type not in self.order_types:
            raise ValueError("The default order type must be one of the outlet's order types")
        return self


class OutletOut(ORM):
    id: uuid.UUID
    brand_id: uuid.UUID
    name: str
    code: str
    gstin: str | None
    fssai: str | None
    address: str | None
    state_code: str | None
    phone: str | None
    bill_footer: str | None
    car_number_required: bool
    order_types: list[str] = ["dine_in", "takeaway", "delivery"]
    default_order_type: str = "takeaway"
    stock_check_frequency_days: int

    @field_validator("order_types", mode="before")
    @classmethod
    def _split(cls, v: object) -> object:
        return [t for t in v.split(",") if t] if isinstance(v, str) else v
    upi_vpa: str | None = None
    upi_payee_name: str | None = None
    qr_ordering: bool = False
    qr_auto_accept: bool = False
    table_labels: list[str] | None = None
    qr_key: str = ""
    kiosk_enabled: bool = False
    print_kots: bool = False
    print_key: str = ""
    is_active: bool
    display_key: str
    kitchen_key: str
    is_training: bool = False
    training_of: uuid.UUID | None = None


# ------------------------------------------------------------------ users

class UserIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    role: Role
    email: str | None = None
    phone: str | None = None
    outlet_ids: list[uuid.UUID] = []
    pin: str | None = None
    is_active: bool = True

    _v_email = field_validator("email")(lambda v: v if not v else normalize_email(v))
    _v_phone = field_validator("phone")(lambda v: v if not v else normalize_phone(v))
    _v_pin = field_validator("pin")(lambda v: v if not v else validate_pin(v))


class UserUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    role: Role | None = None
    email: str | None = None
    phone: str | None = None
    outlet_ids: list[uuid.UUID] | None = None
    is_active: bool | None = None

    _v_email = field_validator("email")(lambda v: v if not v else normalize_email(v))
    _v_phone = field_validator("phone")(lambda v: v if not v else normalize_phone(v))


class PinSet(BaseModel):
    pin: str

    _v_pin = field_validator("pin")(lambda v: validate_pin(v))


class UserOut(ORM):
    id: uuid.UUID
    name: str
    role: str
    email: str | None
    phone: str | None
    is_active: bool
    has_pin: bool = False
    is_screen: bool = False
    outlet_ids: list[uuid.UUID] = []


# ------------------------------------------------------------------ menu

class TaxGroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    cgst_bp: int = Field(default=250, ge=0, le=10000)
    sgst_bp: int = Field(default=250, ge=0, le=10000)
    igst_bp: int = Field(default=0, ge=0, le=10000)
    is_inclusive: bool = False
    is_default: bool = False
    is_active: bool = True


class TaxGroupOut(ORM, TaxGroupIn):
    id: uuid.UUID


class CategoryIn(BaseModel):
    brand_id: uuid.UUID
    name: str = Field(min_length=1, max_length=80)
    parent_id: uuid.UUID | None = None
    color: str | None = None
    sort_order: int = 0
    is_active: bool = True

    _v_color = field_validator("color")(lambda v: v if v is None else validate_hex_color(v))


class CategoryOut(ORM):
    id: uuid.UUID
    brand_id: uuid.UUID
    name: str
    parent_id: uuid.UUID | None
    color: str | None
    sort_order: int
    is_active: bool


class VariantIn(BaseModel):
    id: uuid.UUID | None = None  # set when updating an existing variant
    name: str = Field(default="Regular", min_length=1, max_length=60)
    sku: str | None = Field(default=None, max_length=40)
    price_paise: int = Field(ge=0, le=10_000_000)
    sort_order: int = 0
    is_active: bool = True


class VariantOut(ORM):
    id: uuid.UUID
    name: str
    sku: str | None
    price_paise: int
    sort_order: int
    is_active: bool


class ItemIn(BaseModel):
    brand_id: uuid.UUID
    category_id: uuid.UUID
    name: str = Field(min_length=1, max_length=120)
    short_code: str | None = Field(default=None, max_length=20)
    food_type: str = Field(default="veg", pattern=r"^(veg|non_veg|egg)$")
    hsn_sac: str | None = Field(default=None, max_length=8)
    tax_group_id: uuid.UUID | None = None
    prep_area_id: uuid.UUID | None = None
    description: str | None = None
    sort_order: int = 0
    is_active: bool = True
    variants: list[VariantIn] = Field(min_length=1)
    addon_group_ids: list[uuid.UUID] = []

    _v_code = field_validator("short_code")(lambda v: v.strip().upper() if v and v.strip() else None)


class ItemOut(ORM):
    id: uuid.UUID
    brand_id: uuid.UUID
    category_id: uuid.UUID
    name: str
    short_code: str | None
    food_type: str
    hsn_sac: str | None
    tax_group_id: uuid.UUID | None
    prep_area_id: uuid.UUID | None
    description: str | None
    image_url: str | None
    sort_order: int
    is_active: bool
    variants: list[VariantOut] = []
    addon_group_ids: list[uuid.UUID] = []


class AddonIn(BaseModel):
    id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=80)
    price_paise: int = Field(default=0, ge=0, le=10_000_000)
    is_active: bool = True


class AddonOut(ORM):
    id: uuid.UUID
    name: str
    price_paise: int
    is_active: bool


class AddonGroupIn(BaseModel):
    brand_id: uuid.UUID
    name: str = Field(min_length=1, max_length=80)
    min_select: int = Field(default=0, ge=0)
    max_select: int = Field(default=1, ge=1)
    is_active: bool = True
    addons: list[AddonIn] = []


class AddonGroupOut(ORM):
    id: uuid.UUID
    brand_id: uuid.UUID
    name: str
    min_select: int
    max_select: int
    is_active: bool
    addons: list[AddonOut] = []


class PrepAreaIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    sort_order: int = 0
    is_active: bool = True
    # LAN printer address, e.g. 192.168.1.50. Empty = printed by the browser print station.
    printer_host: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9.\-]*$")
    printer_port: int = Field(default=9100, ge=1, le=65535)
    paper_mm: int = Field(default=80)
    kot_copies: int = Field(default=1, ge=1, le=3)

    _v_host = field_validator("printer_host", mode="before")(lambda v: (v or "").strip() or None)

    @field_validator("paper_mm")
    @classmethod
    def _paper(cls, v: int) -> int:
        if v not in (58, 80):
            raise ValueError("Paper width is 58 or 80 mm")
        return v


class PrepAreaOut(ORM, PrepAreaIn):
    id: uuid.UUID
    station_seen_at: datetime | None = None
    last_print_ok_at: datetime | None = None
    last_print_error: str | None = None
    last_print_error_at: datetime | None = None


class PrintPrinter(BaseModel):
    host: str | None
    port: int
    paper_mm: int
    copies: int


class PrintJob(BaseModel):
    kot_id: uuid.UUID
    kind: str  # kot | reprint | cancel
    prep_area_id: uuid.UUID | None
    printer: PrintPrinter | None  # host None = the browser station's own printer; None = KOT with no area
    printed_before: int
    taken_by: str | None  # captain / cashier who sent it
    kot: KotOut


class PrintFeed(BaseModel):
    outlet_name: str
    app_name: str
    enabled: bool
    areas: list[PrepAreaOut]
    jobs: list[PrintJob]
    server_time: datetime


class PrintResult(BaseModel):
    kind: str = Field(pattern=r"^(kot|reprint|cancel)$")
    ok: bool = True
    error: str | None = Field(default=None, max_length=200)


class BrandMenuOut(BaseModel):
    brand_id: uuid.UUID
    categories: list[CategoryOut]
    items: list[ItemOut]
    addon_groups: list[AddonGroupOut]
    tax_groups: list[TaxGroupOut]
    prep_areas: list[PrepAreaOut] = []


class OverrideIn(BaseModel):
    is_active: bool | None = None
    price_paise: int | None = Field(default=None, ge=0, le=10_000_000)


class OutletMenuVariant(BaseModel):
    variant_id: uuid.UUID
    name: str
    master_price_paise: int
    price_paise: int
    available: bool
    overridden: bool
    sold_out: bool = False


class OutletMenuItem(BaseModel):
    item_id: uuid.UUID
    category_id: uuid.UUID
    name: str
    short_code: str | None
    food_type: str
    tax_group_id: uuid.UUID | None
    prep_area_id: uuid.UUID | None = None
    image_url: str | None = None
    description: str | None = None
    variants: list[OutletMenuVariant]
    addon_groups: list[AddonGroupOut] = []


class OutletMenuOut(BaseModel):
    outlet_id: uuid.UUID
    brand_id: uuid.UUID
    categories: list[CategoryOut]
    items: list[OutletMenuItem]


class ImportIssue(BaseModel):
    sheet: str
    row: int
    message: str


class ImportResult(BaseModel):
    dry_run: bool
    ok: bool
    summary: dict
    errors: list[ImportIssue]
    warnings: list[ImportIssue]
    created: dict | None = None


# ------------------------------------------------------------------ orders, KOT, KDS

class OrderLineIn(BaseModel):
    variant_id: uuid.UUID
    qty: int = Field(default=1, ge=1, le=999)
    addon_ids: list[uuid.UUID] = []
    notes: str | None = Field(default=None, max_length=120)


class OrderCreate(BaseModel):
    id: uuid.UUID | None = None  # device-generated, so a retried request never creates a duplicate
    outlet_id: uuid.UUID
    order_type: str = Field(pattern=r"^(dine_in|takeaway|delivery)$")
    table_label: str | None = Field(default=None, max_length=20)
    car_number: str | None = None
    customer_name: str | None = Field(default=None, max_length=60)
    notes: str | None = Field(default=None, max_length=200)
    items: list[OrderLineIn] = Field(min_length=1, max_length=100)


class QuoteIn(BaseModel):
    outlet_id: uuid.UUID
    items: list[OrderLineIn] = Field(min_length=1, max_length=100)
    discount_paise: int = Field(default=0, ge=0)    # e.g. loyalty points the customer will use


class AddItems(BaseModel):
    items: list[OrderLineIn] = Field(min_length=1, max_length=100)


class CancelIn(BaseModel):
    reason: str = Field(min_length=3, max_length=200)
    manager_pin: str | None = Field(default=None, min_length=4, max_length=4)  # not needed for owners/managers


class StatusChange(BaseModel):
    status: str = Field(pattern=r"^(new|preparing|ready|served)$")


class KotLineOut(BaseModel):
    id: uuid.UUID
    item_name: str
    variant_name: str
    food_type: str
    qty: int
    addons: list[dict] = []
    notes: str | None


class KotOut(BaseModel):
    id: uuid.UUID
    order_id: uuid.UUID
    kot_no: int
    prep_area_id: uuid.UUID | None
    prep_area_name: str
    status: str
    created_at: datetime
    started_at: datetime | None
    ready_at: datetime | None
    age_level: str = "ok"
    token_no: int
    order_type: str
    table_label: str | None
    car_number: str | None
    customer_name: str | None
    order_notes: str | None
    is_addon_kot: bool = False  # items added after the first KOT
    printed: bool = False
    lines: list[KotLineOut]


class OrderOut(BaseModel):
    id: uuid.UUID
    outlet_id: uuid.UUID
    business_date: date
    token_no: int | None
    source: str = "staff"
    customer_phone: str | None = None
    pending_lines: list[dict] = []      # QR order waiting for acceptance: [{name, variant, qty, addons, notes}]
    merged_into_existing: bool = False  # dine-in items joined the table's open order
    payment_status: str | None = None
    pay_method: str | None = None
    quoted_total_paise: int | None = None
    pay_ref: str | None = None
    order_type: str
    table_label: str | None
    car_number: str | None
    customer_name: str | None
    notes: str | None
    status: str
    created_at: datetime
    ready_at: datetime | None
    completed_at: datetime | None
    kots: list[KotOut]
    total_paise: int
    bill_id: uuid.UUID | None = None
    invoice_no: str | None = None
    cancel_reason: str | None = None


class QrOrderIn(BaseModel):
    table: str | None = Field(default=None, max_length=20)
    takeaway: bool = False
    customer_name: str = Field(min_length=1, max_length=60)
    customer_phone: str
    car_number: str | None = None
    notes: str | None = Field(default=None, max_length=200)
    items: list[OrderLineIn] = Field(min_length=1, max_length=40)

    _v_phone = field_validator("customer_phone")(lambda v: normalize_phone(v))


class QrOrderStatus(BaseModel):
    order_id: uuid.UUID
    status: str                          # pending | new | preparing | ready | completed | cancelled
    token_no: int | None
    table: str | None
    message: str | None = None
    total_paise: int
    payment_status: str | None = None
    pay_ref: str | None = None
    upi_link: str | None = None


class KioskQuoteIn(BaseModel):
    items: list[OrderLineIn] = Field(min_length=1, max_length=40)


class KioskOrderIn(KioskQuoteIn):
    order_type: str = Field(pattern=r"^(dine_in|takeaway)$")
    customer_name: str = Field(min_length=1, max_length=60)
    customer_phone: str
    car_number: str | None = None
    pay: str = Field(pattern=r"^(upi|counter)$")

    _v_phone = field_validator("customer_phone")(lambda v: normalize_phone(v))


class AcceptPaidIn(BaseModel):
    mode: str = Field(pattern=r"^(upi|cash|card)$")
    reference: str | None = Field(default=None, max_length=60)


class RejectIn(BaseModel):
    reason: str = Field(min_length=3, max_length=200)


class DisplayOrder(BaseModel):
    token_no: int
    order_type: str
    car_number: str | None
    table_label: str | None


class DisplayOut(BaseModel):
    outlet_name: str
    app_name: str
    logo_url: str | None
    primary_color: str
    show_powered_by: bool = True
    preparing: list[DisplayOrder]
    ready: list[DisplayOrder]
    server_time: datetime


# ------------------------------------------------------------------ billing

class DiscountIn(BaseModel):
    kind: str = Field(pattern=r"^(percent|flat)$")
    value: int = Field(ge=0)            # basis points for percent (10% = 1000), paise for flat
    reason: str | None = Field(default=None, max_length=120)


class CustomerIn(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    phone: str | None = None
    declined: bool = False              # customer didn't want to share a number
    gstin: str | None = None            # for a B2B bill

    _v_phone = field_validator("phone")(lambda v: v if not v else normalize_phone(v))
    _v_gstin = field_validator("gstin")(lambda v: validate_gstin(v))

    @model_validator(mode="after")
    def _no_number_is_fine(self) -> "CustomerIn":
        # The mobile number is optional: a bill without one counts as "no number given".
        if not self.phone:
            self.phone = None
            self.declined = True
        return self


class PaymentLineIn(BaseModel):
    mode: str = Field(pattern=r"^(cash|upi|card|due|complimentary)$")
    amount_paise: int = Field(ge=0)
    reference: str | None = Field(default=None, max_length=60)


class LoyaltyUseIn(BaseModel):
    points_paise: int = Field(default=0, ge=0)   # points to use, in paise (whole rupees)
    use_reward: bool = False                     # the visit-stamp reward
    phone: str | None = None                     # whose points (for a preview before the customer is set)

    _v_phone = field_validator("phone")(lambda v: v if not v else normalize_phone(v))


class BillPreviewIn(BaseModel):
    order_id: uuid.UUID
    discount: DiscountIn | None = None
    loyalty: LoyaltyUseIn | None = None          # used instead of a discount


class BillCreate(BillPreviewIn):
    id: uuid.UUID | None = None         # device-generated for safe retries
    customer: CustomerIn = CustomerIn()
    payments: list[PaymentLineIn] = Field(min_length=1, max_length=4)
    manager_pin: str | None = Field(default=None, min_length=4, max_length=4)


class BillLineOut(ORM):
    id: uuid.UUID | None = None         # set on saved bills (used to correct a line)
    item_name: str
    variant_name: str
    addons_text: str | None
    hsn_sac: str | None
    qty: int
    unit_paise: int
    gross_paise: int
    discount_paise: int
    taxable_paise: int
    cgst_paise: int
    sgst_paise: int
    igst_paise: int
    rate_bp: int
    tax_inclusive: bool


class PaymentOut(ORM):
    mode: str
    amount_paise: int
    reference: str | None


class BillPreviewOut(BaseModel):
    lines: list[BillLineOut]
    gross_paise: int
    discount_paise: int
    taxable_paise: int
    cgst_paise: int
    sgst_paise: int
    igst_paise: int
    round_off_paise: int
    total_paise: int
    tax_summary: list[dict]
    needs_approval: list[str] = []      # reasons a manager PIN is needed


class BillOut(BillPreviewOut):
    training: bool = False              # practice bill: printed "TRAINING - NOT A BILL"
    id: uuid.UUID
    invoice_no: str
    status: str
    order_id: uuid.UUID
    token_no: int | None
    order_type: str
    table_label: str | None
    car_number: str | None
    business_date: date
    created_at: datetime
    customer_name: str | None
    customer_phone: str | None
    customer_gstin: str | None
    discount_reason: str | None
    payments: list[PaymentOut]
    cashier_name: str | None = None
    cancel_reason: str | None = None
    cancelled_at: datetime | None = None
    outlet: dict = {}                   # name, address, gstin, fssai, phone, footer for the printed bill
    upi_link: str | None = None
    revision: int = 1                   # >1: corrected after issue (printed as "Revised")
    edited_at: datetime | None = None
    locked: bool = False                # in a period closed for GST; can't be corrected


class BillLineEdit(BaseModel):
    line_id: uuid.UUID
    qty: int = Field(ge=0, le=999)      # 0 removes the line


class BillFix(BaseModel):
    """The correction for one bill. Leave a part out to keep it as it is."""
    bill_id: uuid.UUID
    revision: int                       # the version being corrected; stops two people overwriting each other
    customer: CustomerIn | None = None
    lines: list[BillLineEdit] = Field(default=[], max_length=100)
    add_items: list[OrderLineIn] = Field(default=[], max_length=50)
    discount: DiscountIn | None = None  # sent = set (value 0 removes it); left out = keep the amount
    payments: list[PaymentLineIn] | None = Field(default=None, max_length=6)
    # Shortcut for a bill paid one way: switch to this mode (the amount follows the corrected total).
    payment_mode: str | None = Field(default=None, pattern=r"^(cash|upi|card)$")


class BillEditBatch(BaseModel):
    """Corrections to one or many bills, saved together: all of them or none."""
    reason: str = Field(min_length=3, max_length=200)
    manager_pin: str | None = Field(default=None, min_length=4, max_length=4)
    bills: list[BillFix] = Field(min_length=1, max_length=50)
    dry_run: bool = False


class BillFixResult(BaseModel):
    bill_id: uuid.UUID
    invoice_no: str
    ok: bool
    error: str | None = None
    preview: BillPreviewOut | None = None  # the bill as it will be
    changes: list[str] = []
    needs_approval: list[str] = []         # empty for managers
    old_total_paise: int = 0
    new_total_paise: int = 0


class BillEditBatchOut(BaseModel):
    saved: bool
    results: list[BillFixResult]
    needs_approval: bool


class BillRevisionOut(BaseModel):
    revision_no: int
    created_at: datetime
    edited_by_name: str | None
    approved_by_name: str | None
    reason: str
    changes: list[str]
    old_total_paise: int
    new_total_paise: int
    before: dict


class BillLockIn(BaseModel):
    through: date | None = None


def _clean_denoms(v: dict[str, int] | None) -> dict[str, int] | None:
    from .core.cash import clean
    return clean(v)


class ShiftOpen(BaseModel):
    outlet_id: uuid.UUID
    drawer_id: uuid.UUID | None = None             # none: the first free drawer
    opening_cash_paise: int = Field(default=0, ge=0, le=100_000_000)
    opening_denoms: dict[str, int] | None = None   # counted note by note; then the amount is their total

    _v_denoms = field_validator("opening_denoms")(lambda v: _clean_denoms(v))


class CashMoveIn(BaseModel):
    """Cash taken out of / put into a drawer, and what it was for.

    Out: expense (category needed, no limit), drawing (handed to the owner or deposited in the bank, with
    who / which bank), supplier (who was paid), other (reason needed). In: topup, change, other (reason needed).
    Old clients send only kind + reason; that is kept as 'other'."""
    kind: str = Field(pattern=r"^(in|out)$")
    amount_paise: int = Field(default=0, ge=0, le=100_000_000)
    denoms: dict[str, int] | None = None     # notes and coins taken out / put in; the total is worked out from them
    purpose: str = "other"
    category: str | None = Field(default=None, max_length=40)
    handed: str | None = None                # drawings: owner | bank
    paid_to: str | None = Field(default=None, max_length=80)
    reason: str = Field(default="", max_length=120)

    @model_validator(mode="after")
    def _purpose_fits(self) -> "CashMoveIn":
        from .core import cash_purpose as cp
        allowed = cp.OUT if self.kind == "out" else cp.IN
        if self.purpose not in allowed:
            raise ValueError(f"Cash {self.kind} can be: {', '.join(allowed)}")
        self.category = (self.category or "").strip() or None
        self.paid_to = (self.paid_to or "").strip() or None
        self.reason = self.reason.strip()
        if self.purpose == "expense" and not self.category:
            raise ValueError("Pick what the expense was for")
        if self.purpose == "drawing":
            if self.handed not in cp.HANDED:
                raise ValueError("Say whether the cash was given to the owner or deposited in the bank")
            if not self.paid_to:
                raise ValueError("Write who took the cash" if self.handed == "owner" else "Write which bank it was deposited in")
        else:
            self.handed = None
        if self.purpose == "supplier" and not self.paid_to:
            raise ValueError("Write which supplier was paid")
        if self.purpose == "other" and len(self.reason) < 2:
            raise ValueError("Write what the cash was for")
        if self.purpose != "expense":
            self.category = None
        return self


class ShiftClose(BaseModel):
    counted_cash_paise: int = Field(default=0, ge=0, le=100_000_000)
    counted_denoms: dict[str, int] | None = None

    _v_denoms = field_validator("counted_denoms")(lambda v: _clean_denoms(v))


class ShiftOut(BaseModel):
    id: uuid.UUID
    outlet_id: uuid.UUID
    status: str
    opened_at: datetime
    opened_by_name: str | None
    opening_cash_paise: int
    opening_denoms: dict[str, int] | None = None
    counted_denoms: dict[str, int] | None = None
    closed_at: datetime | None
    cash_sales_paise: int | None   # hidden from cashiers while the shift is open (blind count)
    cash_in_paise: int
    cash_out_paise: int
    expected_cash_paise: int | None
    counted_cash_paise: int | None
    variance_paise: int | None
    bills: int
    sales_paise: int
    by_mode: dict[str, int]
    expected_opening_paise: int | None = None   # what the last close left in the drawer
    manager_alerted: bool = False                # this count didn't match; the manager was told
    opening_difference_paise: int | None = None
    movements: list[dict] = []                   # cash in / out this shift: kind, amount, notes and coins, reason
    drawer_id: uuid.UUID | None = None
    drawer_name: str | None = None
    closed_by_name: str | None = None
    opened_by_id: uuid.UUID | None = None


TokenOut.model_rebuild()
PrintJob.model_rebuild()
PrintFeed.model_rebuild()


class SmsSettingsIn(BaseModel):
    enabled: bool = False
    provider: str = Field(default="log", pattern=r"^(msg91|log)$")
    msg91_auth_key: str | None = Field(default=None, max_length=80)  # left out / null = keep the saved key
    sender_id: str | None = Field(default=None, pattern=r"^[A-Z]{6}$")
    template_ids: dict[str, str] = {}
    send_bill: bool = True
    send_ready: bool = True
    send_daily: bool = True
    send_cash: bool = True
    send_alerts: bool = True
    alert_discount_bp: int = Field(default=2000, ge=500, le=10000)
    daily_to: list[str] = Field(default=[], max_length=5)

    _v_daily = field_validator("daily_to")(lambda v: [normalize_phone(p) for p in v if p.strip()])

    @field_validator("template_ids")
    @classmethod
    def _templates(cls, v: dict[str, str]) -> dict[str, str]:
        allowed = {"bill", "ready", "daily", "cash", "test"}
        if set(v) - allowed:
            raise ValueError("Unknown message kind")
        return {k: t.strip() for k, t in v.items() if t and t.strip()}


class SmsSettingsOut(BaseModel):
    enabled: bool
    provider: str
    has_auth_key: bool
    sender_id: str | None
    template_ids: dict[str, str]
    send_bill: bool
    send_ready: bool
    send_daily: bool
    send_cash: bool = True
    send_alerts: bool = True
    alert_discount_bp: int = 2000
    daily_to: list[str]
    templates: dict[str, dict]       # the texts to register on DLT / MSG91


class SmsTestIn(BaseModel):
    phone: str

    _v_phone = field_validator("phone")(lambda v: normalize_phone(v))


class SmsMessageOut(ORM):
    id: uuid.UUID
    kind: str
    to_phone: str
    body: str
    status: str
    attempts: int
    last_error: str | None
    created_at: datetime
    sent_at: datetime | None


class PrepAreaAssign(BaseModel):
    item_ids: list[uuid.UUID] = Field(min_length=1, max_length=2000)
    prep_area_id: uuid.UUID | None = None   # None = the default kitchen


class CashAlertOut(BaseModel):
    id: uuid.UUID
    outlet_name: str
    kind: str
    cashier_name: str | None
    expected_paise: int
    counted_paise: int
    difference_paise: int
    denoms: dict | None
    created_at: datetime
    checked_by_name: str | None
    checked_at: datetime | None
    note: str | None


class CashAlertCheck(BaseModel):
    note: str = Field(min_length=2, max_length=300)


class CashTolerance(BaseModel):
    tolerance_paise: int = Field(ge=0, le=100_000)
