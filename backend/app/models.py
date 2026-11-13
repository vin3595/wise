"""SQLAlchemy models. Money is integer paise; tax rates are basis points.

Every tenant-owned table has tenant_id and is protected by Postgres row-level security
(see alembic/versions/0001_initial.py). Primary keys are UUIDs so offline devices can
create records without collisions (needed from Phase 2 billing onward).
"""
from __future__ import annotations

import uuid
import secrets
from datetime import date, datetime

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def tenant_fk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False)


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


# ---------------------------------------------------------------- tenancy

class Tenant(Base, Timestamps):
    __tablename__ = "tenants"
    id: Mapped[uuid.UUID] = pk()
    login_id: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    owner_email: Mapped[str] = mapped_column(String(254), nullable=False)
    owner_phone: Mapped[str] = mapped_column(String(15), nullable=False)
    gstin: Mapped[str | None] = mapped_column(String(15))
    # White-label branding
    app_name: Mapped[str | None] = mapped_column(String(60))
    logo_url: Mapped[str | None] = mapped_column(String(500))
    primary_color: Mapped[str] = mapped_column(String(7), default="#2563eb", nullable=False)
    # Look of the staff app: steel | board | irani | swiss | shake (Setup > Branding).
    ui_theme: Mapped[str] = mapped_column(String(12), default="steel", server_default="steel", nullable=False)
    custom_domain: Mapped[str | None] = mapped_column(String(253), unique=True)
    show_powered_by: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Subscription
    plan: Mapped[str] = mapped_column(String(20), default="trial", nullable=False)
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)  # active|read_only|suspended
    fy_start_month: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    # Discounts above this share of the bill need a manager PIN (basis points; 1000 = 10%).
    discount_limit_bp: Mapped[int] = mapped_column(Integer, default=1000, nullable=False)
    # Bills on or before this date can't be corrected (set after the GST return for that period is filed).
    bills_locked_through: Mapped[date | None] = mapped_column(Date)
    # Cash differences up to this much (e.g. small coins) don't alert the manager. 0 = every rupee.
    cash_tolerance_paise: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Brand(Base, Timestamps):
    __tablename__ = "brands"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    logo_url: Mapped[str | None] = mapped_column(String(500))
    primary_color: Mapped[str | None] = mapped_column(String(7))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)


class Outlet(Base, Timestamps):
    __tablename__ = "outlets"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("brands.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Invoice prefix: CODE/2627/000123 must stay within 16 characters (GST Rule 46), so 2-4 characters.
    code: Mapped[str] = mapped_column(String(4), nullable=False)
    gstin: Mapped[str | None] = mapped_column(String(15))
    fssai: Mapped[str | None] = mapped_column(String(14))
    address: Mapped[str | None] = mapped_column(Text)
    state_code: Mapped[str | None] = mapped_column(String(2))
    phone: Mapped[str | None] = mapped_column(String(15))
    bill_footer: Mapped[str | None] = mapped_column(String(300))
    upi_vpa: Mapped[str | None] = mapped_column(String(80))        # e.g. shakewallah@okaxis, for the bill QR
    upi_payee_name: Mapped[str | None] = mapped_column(String(60))
    car_number_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Which order types this outlet takes (a delivery-only kitchen, a dine-in cafe...) and which one the
    # billing screen starts on.
    order_types: Mapped[str] = mapped_column(String(40), default="dine_in,takeaway,delivery",
                                             server_default="dine_in,takeaway,delivery", nullable=False)
    default_order_type: Mapped[str] = mapped_column(String(10), default="takeaway", server_default="takeaway",
                                                    nullable=False)
    # QR ordering: customers scan /q/<qr_key>?t=<table>. Orders wait for staff to accept unless auto-accept is on.
    qr_key: Mapped[str] = mapped_column(String(32), unique=True, nullable=False,
                                        default=lambda: secrets.token_urlsafe(12))
    qr_ordering: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    qr_auto_accept: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    kiosk_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # /kiosk/<qr_key>
    table_labels: Mapped[list | None] = mapped_column(JSONB)  # ["T1", "T2", ...]
    stock_check_frequency_days: Mapped[int] = mapped_column(Integer, default=7, nullable=False)
    # Secret part of the customer order-ready screen URL (/display/<key>); no login on the TV.
    display_key: Mapped[str] = mapped_column(
        String(32), unique=True, nullable=False, default=lambda: secrets.token_urlsafe(16))
    # KOT printing: the print bridge / print station signs in with this key (no staff login on that PC).
    print_kots: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    print_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    print_key: Mapped[str] = mapped_column(
        String(32), unique=True, nullable=False, default=lambda: secrets.token_urlsafe(16))
    # Kitchen display without a daily PIN: the screen opens /kitchen/<key> and signs in as the outlet's kitchen screen.
    kitchen_key: Mapped[str] = mapped_column(
        String(32), unique=True, nullable=False, default=lambda: secrets.token_urlsafe(16))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Training: a copy of a real outlet where staff practise. Nothing there is real: no stock or loyalty
    # on real customers, no SMS, no real UPI/card, and it's left out of reports, GST and Tally.
    is_training: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    training_of: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("outlets.id"))
    __table_args__ = (UniqueConstraint("tenant_id", "code"),)


class User(Base, Timestamps):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str | None] = mapped_column(String(254))
    phone: Mapped[str | None] = mapped_column(String(15))
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    pin_hash: Mapped[str | None] = mapped_column(String(200))
    # HMAC(server secret, tenant:pin) - lets PIN login find the user without scanning every hash.
    pin_lookup: Mapped[str | None] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # A screen, not a person (the kitchen display's own sign-in). Hidden from Staff; can't sign in with a PIN.
    is_screen: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    __table_args__ = (
        UniqueConstraint("tenant_id", "email"),
        UniqueConstraint("tenant_id", "phone"),
        UniqueConstraint("tenant_id", "pin_lookup"),
        CheckConstraint("role in ('owner','manager','cashier','captain','kitchen')", name="ck_user_role"),
    )


class UserOutlet(Base):
    __tablename__ = "user_outlets"
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    outlet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("outlets.id", ondelete="CASCADE"), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = tenant_fk()


# ---------------------------------------------------------------- auth (not tenant-scoped)

class OtpCode(Base):
    __tablename__ = "otp_codes"
    id: Mapped[uuid.UUID] = pk()
    identifier: Mapped[str] = mapped_column(String(254), index=True, nullable=False)  # phone or email
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)  # signup | login
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AuthThrottle(Base):
    __tablename__ = "auth_throttle"
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# ---------------------------------------------------------------- menu

class TaxGroup(Base, Timestamps):
    __tablename__ = "tax_groups"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    cgst_bp: Mapped[int] = mapped_column(Integer, default=250, nullable=False)
    sgst_bp: Mapped[int] = mapped_column(Integer, default=250, nullable=False)
    igst_bp: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_inclusive: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)


class PrepArea(Base, Timestamps):
    """Shake counter, Kitchen, Ice cream... Each gets its own KOT and KDS screen."""
    __tablename__ = "prep_areas"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Its KOT printer. A network (LAN/Wi-Fi) thermal printer is reached by the print bridge on
    # printer_host:printer_port; with no host, the browser print station prints on its own printer.
    printer_host: Mapped[str | None] = mapped_column(String(64))
    printer_port: Mapped[int] = mapped_column(Integer, default=9100, nullable=False)
    paper_mm: Mapped[int] = mapped_column(Integer, default=80, nullable=False)  # 58 or 80
    kot_copies: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    station_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_print_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_print_error: Mapped[str | None] = mapped_column(String(200))
    last_print_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("tenant_id", "name"),
                      CheckConstraint("paper_mm in (58, 80)", name="ck_prep_paper"),
                      CheckConstraint("kot_copies between 1 and 3", name="ck_prep_copies"))


class Category(Base, Timestamps):
    __tablename__ = "categories"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("brands.id"), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("categories.id"))
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    color: Mapped[str | None] = mapped_column(String(7))
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    __table_args__ = (UniqueConstraint("brand_id", "name"),)


class Item(Base, Timestamps):
    __tablename__ = "items"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("brands.id"), nullable=False)
    category_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("categories.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    short_code: Mapped[str | None] = mapped_column(String(20))
    food_type: Mapped[str] = mapped_column(String(10), default="veg", nullable=False)
    hsn_sac: Mapped[str | None] = mapped_column(String(8))
    tax_group_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tax_groups.id"))
    # Which preparation area makes it: decides which KOT / KDS screen the item goes to.
    prep_area_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("prep_areas.id"))
    # Deleted items leave every menu and the menu editor; old bills and reports still show them.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    description: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(String(500))
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    __table_args__ = (
        UniqueConstraint("brand_id", "name"),
        UniqueConstraint("brand_id", "short_code"),
        CheckConstraint("food_type in ('veg','non_veg','egg')", name="ck_item_food_type"),
    )


class Variant(Base, Timestamps):
    __tablename__ = "variants"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("items.id", ondelete="CASCADE"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    sku: Mapped[str | None] = mapped_column(String(40))
    price_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    __table_args__ = (
        UniqueConstraint("item_id", "name"),
        CheckConstraint("price_paise >= 0", name="ck_variant_price"),
    )


class AddonGroup(Base, Timestamps):
    __tablename__ = "addon_groups"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("brands.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    min_select: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_select: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    __table_args__ = (
        UniqueConstraint("brand_id", "name"),
        CheckConstraint("min_select >= 0 and max_select >= 1 and min_select <= max_select", name="ck_addon_minmax"),
    )


class Addon(Base, Timestamps):
    __tablename__ = "addons"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("addon_groups.id", ondelete="CASCADE"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    price_paise: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    __table_args__ = (UniqueConstraint("group_id", "name"), CheckConstraint("price_paise >= 0", name="ck_addon_price"))


class ItemAddonGroup(Base):
    __tablename__ = "item_addon_groups"
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("items.id", ondelete="CASCADE"), primary_key=True)
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("addon_groups.id", ondelete="CASCADE"), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = tenant_fk()


class OutletVariantOverride(Base, Timestamps):
    __tablename__ = "outlet_variant_overrides"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    outlet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("outlets.id", ondelete="CASCADE"), nullable=False)
    variant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("variants.id", ondelete="CASCADE"), nullable=False)
    is_active: Mapped[bool | None] = mapped_column(Boolean)
    price_paise: Mapped[int | None] = mapped_column(Integer)
    # "Sold out" for the rest of this business day only (tapped at the counter or in the kitchen).
    sold_out_on: Mapped[date | None] = mapped_column(Date)
    __table_args__ = (
        UniqueConstraint("outlet_id", "variant_id"),
        CheckConstraint("price_paise is null or price_paise >= 0", name="ck_override_price"),
    )


# ---------------------------------------------------------------- orders and kitchen tickets
# Phase 2 (billing) adds payments, invoice numbers and taxes on top of these tables.

class DailyCounter(Base):
    """Token and KOT numbers that restart every business day per outlet."""
    __tablename__ = "daily_counters"
    outlet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("outlets.id", ondelete="CASCADE"), primary_key=True)
    business_date: Mapped[date] = mapped_column(Date, primary_key=True)
    kind: Mapped[str] = mapped_column(String(10), primary_key=True)  # token | kot
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    last_no: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class OrderRequest(Base):
    """Remembers device-generated request ids so a retried 'send to kitchen' never sends twice,
    including when the items were merged into a table's existing order."""
    __tablename__ = "order_requests"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"),
                                                nullable=False)
    merged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Order(Base, Timestamps):
    __tablename__ = "orders"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    outlet_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("outlets.id"), nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    token_no: Mapped[int | None] = mapped_column(Integer)  # given when the order reaches the kitchen
    order_type: Mapped[str] = mapped_column(String(10), nullable=False)
    source: Mapped[str] = mapped_column(String(8), default="staff", nullable=False)  # staff | qr | kiosk | offline
    # Kiosk self-orders: the customer pays first (UPI) or at the counter; KOTs go out once it's paid.
    payment_status: Mapped[str | None] = mapped_column(String(10))   # awaiting | paid
    pay_method: Mapped[str | None] = mapped_column(String(8))        # upi | counter
    quoted_total_paise: Mapped[int | None] = mapped_column(Integer)
    pay_ref: Mapped[str | None] = mapped_column(String(12))          # short code shown on screen and in the UPI note
    customer_phone: Mapped[str | None] = mapped_column(String(15))
    pending_items: Mapped[list | None] = mapped_column(JSONB)  # QR order lines waiting for acceptance
    merged_into_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("orders.id"))
    table_label: Mapped[str | None] = mapped_column(String(20))
    car_number: Mapped[str | None] = mapped_column(String(10), index=True)
    customer_name: Mapped[str | None] = mapped_column(String(60))
    notes: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(12), default="new", nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(String(200))
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    cancel_approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    __table_args__ = (
        UniqueConstraint("outlet_id", "business_date", "token_no"),
        CheckConstraint("order_type in ('dine_in','takeaway','delivery')", name="ck_order_type"),
        CheckConstraint("status in ('pending','new','preparing','ready','completed','cancelled')",
                        name="ck_order_status"),
        Index("ix_orders_outlet_status", "outlet_id", "status"),
        Index("ix_orders_outlet_day_status", "outlet_id", "business_date", "status"),   # open orders, every few s
    )


class Kot(Base, Timestamps):
    __tablename__ = "kots"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), index=True, nullable=False)
    outlet_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("outlets.id"), nullable=False)
    prep_area_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("prep_areas.id"))
    prep_area_name: Mapped[str] = mapped_column(String(40), nullable=False)  # snapshot for the printed slip
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    kot_no: Mapped[int] = mapped_column(Integer, nullable=False)
    round_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)  # 2+ = items added later
    status: Mapped[str] = mapped_column(String(10), default="new", nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    served_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Printing. A job is claimed by one station for a short lease so two stations never print it twice;
    # if the station dies before confirming, the lease runs out and another poll prints it.
    printed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    print_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reprint_pending: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cancel_printed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    print_claim: Mapped[str | None] = mapped_column(String(40))
    print_claim_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("outlet_id", "business_date", "kot_no"),
        CheckConstraint("status in ('new','preparing','ready','served','cancelled')", name="ck_kot_status"),
        Index("ix_kots_outlet_status", "outlet_id", "status"),
        Index("ix_kots_outlet_day_status", "outlet_id", "business_date", "status"),     # kitchen screens
    )


class OrderItem(Base):
    __tablename__ = "order_items"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), index=True, nullable=False)
    kot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("kots.id", ondelete="CASCADE"), index=True, nullable=False)
    variant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("variants.id"), nullable=False)
    # Snapshots so later menu edits never change what the kitchen saw or the bill shows.
    item_name: Mapped[str] = mapped_column(String(120), nullable=False)
    variant_name: Mapped[str] = mapped_column(String(60), nullable=False)
    food_type: Mapped[str] = mapped_column(String(10), nullable=False)
    unit_price_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    addons: Mapped[list | None] = mapped_column(JSONB)  # [{id, name, price_paise}]
    notes: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (CheckConstraint("qty > 0 and qty <= 999", name="ck_order_item_qty"),)


# ---------------------------------------------------------------- billing

class RolePermission(Base):
    """A restaurant's own choice of what a role may do (no row: the defaults in core/permissions.py).
    The owner always has everything."""
    __tablename__ = "role_permissions"
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(10), primary_key=True)
    perms: Mapped[list] = mapped_column(JSONB, nullable=False)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Drawer(Base):
    """A cash drawer at an outlet. One shift uses a drawer at a time, and each drawer's next shift
    starts from what its last shift left in it, so two cashiers' cash never mixes."""
    __tablename__ = "drawers"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    outlet_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("outlets.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(30), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (UniqueConstraint("outlet_id", "name"),)


class Shift(Base):
    """A cashier's session at an outlet: opening cash, cash in/out, closing count."""
    __tablename__ = "shifts"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    outlet_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("outlets.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(8), default="open", nullable=False)
    opened_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    drawer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("drawers.id"), index=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    opening_cash_paise: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    opening_denoms: Mapped[dict | None] = mapped_column(JSONB)   # {"n500": 2, "c10": 5, ...} when counted by note
    # Carry-forward: the cash the previous close left in the drawer is what this shift should open with.
    carried_from_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("shifts.id"), unique=True)
    expected_opening_paise: Mapped[int | None] = mapped_column(Integer)
    counted_denoms: Mapped[dict | None] = mapped_column(JSONB)
    closed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expected_cash_paise: Mapped[int | None] = mapped_column(Integer)
    counted_cash_paise: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (
        CheckConstraint("status in ('open','closed')", name="ck_shift_status"),
        # Each cashier has their own shift (and cash drawer); several can bill at one outlet at once.
        Index("uq_shift_open_per_cashier", "outlet_id", "opened_by", unique=True,
              postgresql_where=text("status = 'open'")),
    )


class CashAlert(Base):
    """A cash count that didn't match what the system expected, for the manager to check."""
    __tablename__ = "cash_alerts"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    outlet_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("outlets.id"), nullable=False)
    shift_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shifts.id"), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(6), nullable=False)    # open | close
    expected_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    counted_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    denoms: Mapped[dict | None] = mapped_column(JSONB)
    cashier_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    checked_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(String(300))


# ---------------------------------------------------------------- inventory

class Supplier(Base, Timestamps):
    __tablename__ = "suppliers"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(15))
    gstin: Mapped[str | None] = mapped_column(String(15))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)


class StockItem(Base, Timestamps):
    """A raw material (milk, sugar, cups) or something made in-house from them (dry fruit crush).
    Quantities are thousandths of the base unit (g, ml or pc); see core/stock.py."""
    __tablename__ = "stock_items"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(4), default="raw", nullable=False)       # raw | prep
    unit: Mapped[str] = mapped_column(String(2), nullable=False)                      # g | ml | pc
    category: Mapped[str | None] = mapped_column(String(40))
    # Units it is bought in: [{"name": "kg", "factor": 1000}, {"name": "box of 24", "factor": 24}]
    purchase_units: Mapped[list | None] = mapped_column(JSONB)
    default_supplier_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.id"))
    min_level_m: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    reorder_qty_m: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    yield_m: Mapped[int | None] = mapped_column(BigInteger)    # prep: how much one batch of its recipe makes
    # prep made fresh for each order (a chutney mixed per plate): no stock of its own; a sale takes its ingredients
    made_on_sale: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    last_cost_k: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)   # latest purchase price
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    __table_args__ = (UniqueConstraint("tenant_id", "name"),
                      CheckConstraint("unit in ('g','ml','pc')", name="ck_stock_unit"),
                      CheckConstraint("kind in ('raw','prep')", name="ck_stock_kind"))


class RecipeLine(Base):
    """What one of something uses: a menu item size (variant), an add-on, or one batch of a prep item."""
    __tablename__ = "recipe_lines"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    owner_kind: Mapped[str] = mapped_column(String(7), nullable=False)    # variant | addon | prep
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    stock_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("stock_items.id"), nullable=False)
    qty_m: Mapped[int] = mapped_column(BigInteger, nullable=False)
    __table_args__ = (UniqueConstraint("owner_kind", "owner_id", "stock_item_id"),
                      Index("ix_recipe_owner", "owner_kind", "owner_id"),
                      CheckConstraint("qty_m > 0", name="ck_recipe_qty"))


class StockLevel(Base):
    """Stock on hand at an outlet, kept up to date with every move (the moves are the full record)."""
    __tablename__ = "stock_levels"
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    outlet_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("outlets.id"), primary_key=True)
    stock_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("stock_items.id"), primary_key=True)
    qty_m: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    cost_k: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)        # weighted average
    min_level_m: Mapped[int | None] = mapped_column(BigInteger)                      # outlet's own minimum
    counted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StockMove(Base):
    """Every change to stock: purchase, sale (by recipe), prep, wastage, count adjustment. Never edited."""
    __tablename__ = "stock_moves"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    outlet_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("outlets.id"), nullable=False)
    stock_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("stock_items.id"), nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)   # purchase | sale | prep_in | prep_out | waste | count
    qty_m: Mapped[int] = mapped_column(BigInteger, nullable=False)  # + in, - out
    cost_k: Mapped[int] = mapped_column(BigInteger, nullable=False)
    value_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ref_kind: Mapped[str | None] = mapped_column(String(8))        # bill | purchase | prep | waste | count
    ref_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    note: Mapped[str | None] = mapped_column(String(200))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (Index("ix_stock_moves_outlet_day", "outlet_id", "business_date"),
                      Index("ix_stock_moves_ref", "ref_kind", "ref_id"),
                      Index("ix_stock_moves_item", "stock_item_id", "business_date"))


class Purchase(Base):
    """A supplier's bill (goods received). Adds stock and updates the average cost. Cancelled, never deleted."""
    __tablename__ = "purchases"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    outlet_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("outlets.id"), nullable=False)
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.id"))
    invoice_no: Mapped[str | None] = mapped_column(String(40))
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)     # before GST
    tax_paise: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    total_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    lines: Mapped[list] = mapped_column(JSONB, nullable=False)
    notes: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(9), default="posted", nullable=False)   # posted | cancelled
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (Index("ix_purchases_outlet_date", "outlet_id", "invoice_date"),)


class StockCount(Base):
    """A closing count. Staff count blind; the manager sees each line's expected stock and difference."""
    __tablename__ = "stock_counts"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    outlet_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("outlets.id"), nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    lines: Mapped[list] = mapped_column(JSONB, nullable=False)   # [{stock_item_id, expected_m, counted_m, value_paise}]
    counted_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DayClose(Base):
    """A closed business day at an outlet: the checks done and the day's figures as they stood (Z report).
    New bills can't be made for a closed day; a manager can reopen it (kept in the audit log)."""
    __tablename__ = "day_closes"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    outlet_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("outlets.id"), nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(8), default="closed", nullable=False)   # closed | reopened
    summary: Mapped[dict] = mapped_column(JSONB, nullable=False)
    warnings: Mapped[list | None] = mapped_column(JSONB)       # what was still open, closed anyway with a note
    note: Mapped[str | None] = mapped_column(String(300))
    closed_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (UniqueConstraint("outlet_id", "business_date"),)


class PaymentSettings(Base):
    """A restaurant's own Razorpay account for UPI that confirms itself. The money goes to their account."""
    __tablename__ = "payment_settings"
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    provider: Mapped[str] = mapped_column(String(10), default="test", nullable=False)   # razorpay | paytm | test (no money)
    key_id: Mapped[str | None] = mapped_column(String(40))
    key_secret: Mapped[str | None] = mapped_column(String(80))
    webhook_secret: Mapped[str | None] = mapped_column(String(80))
    # Secret part of this restaurant's webhook URL (/api/public/razorpay/<key>).
    webhook_key: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, default=lambda: secrets.token_urlsafe(16))
    # Razorpay POS card machines (a separate Razorpay POS account: username + app key per restaurant).
    pos_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    pos_provider: Mapped[str] = mapped_column(String(12), default="test", server_default="test", nullable=False)  # razorpay_pos | test
    pos_live: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)       # False: Razorpay's demo server
    pos_username: Mapped[str | None] = mapped_column(String(40))
    pos_app_key: Mapped[str | None] = mapped_column(String(80))
    # Paytm Dynamic QR (the restaurant's own Paytm for Business MID and merchant key).
    paytm_mid: Mapped[str | None] = mapped_column(String(40))
    paytm_key: Mapped[str | None] = mapped_column(String(40))
    paytm_client_id: Mapped[str | None] = mapped_column(String(8))
    paytm_live: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    # How long a QR stays payable. Paytm's default is 10 minutes; longer needs Paytm to allow it.
    qr_minutes: Mapped[int] = mapped_column(Integer, default=10, server_default="10", nullable=False)
    # Print the QR on a slip the moment the cashier picks UPI (for tables, or a counter without a screen facing the customer).
    print_qr: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CardTerminal(Base):
    """A card machine at an outlet (Razorpay POS device id, e.g. '1490012345|ezetap_android')."""
    __tablename__ = "card_terminals"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    outlet_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("outlets.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(30), nullable=False)
    device_id: Mapped[str] = mapped_column(String(60), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    __table_args__ = (UniqueConstraint("outlet_id", "name"),)


class AdSlide(Base):
    """A picture or short video shown on the customer screen (second monitor) while the counter is idle."""
    __tablename__ = "ad_slides"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    outlet_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("outlets.id"))   # empty = every outlet
    kind: Mapped[str] = mapped_column(String(5), nullable=False)          # image | video
    url: Mapped[str] = mapped_column(String(300), nullable=False)
    title: Mapped[str | None] = mapped_column(String(80))
    seconds: Mapped[int] = mapped_column(Integer, default=8, nullable=False)   # how long a picture stays (videos play to the end)
    sort: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    starts_on: Mapped[date | None] = mapped_column(Date)
    ends_on: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CardRequest(Base):
    """One amount pushed to a card machine for one bill; the bill is made when the machine approves."""
    __tablename__ = "card_requests"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    outlet_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("outlets.id"), nullable=False)
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orders.id"), index=True, nullable=False)
    terminal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("card_terminals.id"), nullable=False)
    amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(String(12), nullable=False)
    provider_ref: Mapped[str | None] = mapped_column(String(60), index=True)      # Razorpay POS p2pRequestId
    status: Mapped[str] = mapped_column(String(10), default="waiting", nullable=False)  # waiting | billed | failed | closed | problem
    payment_ref: Mapped[str | None] = mapped_column(String(60))                   # card last 4 and auth code
    txn_id: Mapped[str | None] = mapped_column(String(60))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bill_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("bills.id"))
    bill_body: Mapped[dict] = mapped_column(JSONB, nullable=False)
    problem: Mapped[str | None] = mapped_column(String(200))
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UpiRequest(Base):
    """One UPI QR shown for one bill. When the payment arrives (Razorpay webhook, or the box asking
    Razorpay), the bill is made by itself with the UPI reference on it."""
    __tablename__ = "upi_requests"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    outlet_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("outlets.id"), nullable=False)
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orders.id"), index=True, nullable=False)
    amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(String(10), nullable=False)
    provider_ref: Mapped[str | None] = mapped_column(String(40), index=True)     # Razorpay QR id / Paytm order id
    upi_string: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), default="waiting", nullable=False)  # waiting | paid | billed | closed | problem
    payment_ref: Mapped[str | None] = mapped_column(String(60))     # Razorpay payment id / UPI reference
    payer: Mapped[str | None] = mapped_column(String(80))           # payer's UPI ID, when given
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bill_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("bills.id"))
    bill_body: Mapped[dict] = mapped_column(JSONB, nullable=False)  # customer and bill id the cashier entered
    problem: Mapped[str | None] = mapped_column(String(200))
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LoyaltySettings(Base):
    """The restaurant's loyalty rules, set by the owner or a manager. Mode off/points/stamps/both."""
    __tablename__ = "loyalty_settings"
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), primary_key=True)
    mode: Mapped[str] = mapped_column(String(6), default="off", nullable=False)
    earn_bp: Mapped[int] = mapped_column(Integer, default=500, nullable=False)
    min_bill_paise: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    redeem_min_paise: Mapped[int] = mapped_column(Integer, default=5000, nullable=False)
    max_redeem_bp: Mapped[int] = mapped_column(Integer, default=5000, nullable=False)
    expiry_days: Mapped[int] = mapped_column(Integer, default=180, nullable=False)
    stamp_min_paise: Mapped[int] = mapped_column(Integer, default=10000, nullable=False)
    stamps_needed: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    reward_paise: Mapped[int] = mapped_column(Integer, default=15000, nullable=False)
    reward_label: Mapped[str] = mapped_column(String(60), default="Free shake (up to ₹150)", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Customer(Base):
    """A customer known by mobile number (from bills where they gave it). Points and stamps are
    kept here as running totals; LoyaltyEntry has every change."""
    __tablename__ = "customers"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    phone: Mapped[str] = mapped_column(String(15), nullable=False)
    name: Mapped[str | None] = mapped_column(String(80))
    points_paise: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    stamps: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    first_visit: Mapped[date | None] = mapped_column(Date)
    last_visit: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (UniqueConstraint("tenant_id", "phone"),)


class LoyaltyEntry(Base):
    __tablename__ = "loyalty_entries"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("customers.id"), index=True, nullable=False)
    bill_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("bills.id"), index=True)
    kind: Mapped[str] = mapped_column(String(7), nullable=False)    # earn | redeem | reward | expire | adjust
    points_paise: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    stamps: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    note: Mapped[str | None] = mapped_column(String(120))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AccountsSettings(Base):
    """How exports for the accountant are named: the Tally company and ledger names (see core/tally.py)."""
    __tablename__ = "accounts_settings"
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), primary_key=True)
    company_name: Mapped[str | None] = mapped_column(String(120))
    ledgers: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CounterDevice(Base):
    """A billing counter allowed to keep billing when it can't reach the box. It owns one invoice
    series (outlet code + letter) and a block of token numbers, so offline bills never collide."""
    __tablename__ = "counter_devices"
    id: Mapped[uuid.UUID] = pk()                      # generated on the device, kept in its storage
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    outlet_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("outlets.id"), nullable=False)
    letter: Mapped[str] = mapped_column(String(1), nullable=False)
    series: Mapped[str] = mapped_column(String(4), nullable=False)
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    registered_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("outlet_id", "letter"), UniqueConstraint("outlet_id", "series"))


class OfflineSync(Base):
    """One offline bill received from a counter, by the id the device gave it (makes sync safe to retry)."""
    __tablename__ = "offline_syncs"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    device_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("counter_devices.id"), nullable=False)
    bill_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("bills.id"), nullable=False)
    notes: Mapped[list | None] = mapped_column(JSONB)   # what differed from the box's menu at sync time
    made_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CashMovement(Base):
    __tablename__ = "cash_movements"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    shift_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shifts.id"), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(3), nullable=False)  # in | out
    amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    denoms: Mapped[dict | None] = mapped_column(JSONB)      # {"n500": 1, "c10": 2}, when counted note by note
    reason: Mapped[str] = mapped_column(String(120), nullable=False)
    # What the cash was for. Out: expense | drawing | supplier | other. In: topup | change | other.
    purpose: Mapped[str | None] = mapped_column(String(10))
    category: Mapped[str | None] = mapped_column(String(40))      # expense category, e.g. Milk
    handed: Mapped[str | None] = mapped_column(String(6))         # drawings: owner | bank
    paid_to: Mapped[str | None] = mapped_column(String(80))       # owner's name, bank, or supplier
    note: Mapped[str | None] = mapped_column(String(120))         # what the cashier typed, as typed
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (CheckConstraint("kind in ('in','out') and amount_paise > 0", name="ck_cash_movement"),)


class Bill(Base):
    """GST invoice. Never deleted. A wrong entry is corrected under the same number: the previous
    version is kept as a BillRevision (with reason, editor and approver) and listed in the Bill edits
    report. Or it is cancelled (the number stays in the series, marked cancelled) and billed again."""
    __tablename__ = "bills"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    outlet_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("outlets.id"), nullable=False)
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orders.id"), index=True, nullable=False)
    shift_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("shifts.id"), index=True, nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    fy_start: Mapped[int] = mapped_column(Integer, nullable=False)
    # "" is the outlet's main series (CODE/2627/000123). Each counter that may bill while the box is down
    # gets its own series (e.g. SHKA/2627/000001), so numbers made offline can never clash with the box's.
    series: Mapped[str] = mapped_column(String(4), default="", server_default="", nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    invoice_no: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="paid", nullable=False)  # paid | cancelled
    gross_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    discount_paise: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    discount_reason: Mapped[str | None] = mapped_column(String(120))
    taxable_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    cgst_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    sgst_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    igst_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    round_off_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    total_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    customer_name: Mapped[str | None] = mapped_column(String(80))
    customer_phone: Mapped[str | None] = mapped_column(String(15), index=True)
    customer_declined_phone: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    customer_gstin: Mapped[str | None] = mapped_column(String(15))   # B2B bill
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    cancel_approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    cancel_reason: Mapped[str | None] = mapped_column(String(200))
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Loyalty used on this bill (already inside discount_paise): points in paise, and the stamp reward.
    loyalty_points_paise: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    loyalty_reward_paise: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    # Random key for the customer's e-bill link (/b/<key>); not guessable from the bill number.
    share_key: Mapped[str | None] = mapped_column(String(16), unique=True)
    __table_args__ = (
        UniqueConstraint("outlet_id", "series", "fy_start", "seq"),
        UniqueConstraint("outlet_id", "invoice_no"),
        CheckConstraint("status in ('paid','cancelled')", name="ck_bill_status"),
        # One live bill per order; a cancelled bill frees the order to be billed again.
        Index("uq_bill_live_per_order", "order_id", unique=True, postgresql_where=text("status = 'paid'")),
        Index("ix_bills_outlet_date", "outlet_id", "business_date"),
    )


class BillRevision(Base):
    """A bill as it was before a correction. Append-only."""
    __tablename__ = "bill_revisions"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    bill_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("bills.id"), index=True, nullable=False)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)  # the version this snapshot is
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)       # full bill as printed (lines, payments)
    old_total_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    new_total_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    changes: Mapped[list] = mapped_column(JSONB, nullable=False)         # ["items", "payment", "customer"]
    reason: Mapped[str] = mapped_column(String(200), nullable=False)
    edited_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (UniqueConstraint("bill_id", "revision_no"),)


class SmsSettings(Base):
    """A restaurant's SMS setup. Each restaurant sends from its own DLT-registered sender ID and
    templates through its own MSG91 account (so the SMS cost is theirs)."""
    __tablename__ = "sms_settings"
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    provider: Mapped[str] = mapped_column(String(10), default="log", nullable=False)  # msg91 | log (testing)
    msg91_auth_key: Mapped[str | None] = mapped_column(String(80))
    sender_id: Mapped[str | None] = mapped_column(String(6))
    template_ids: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)  # kind -> MSG91 template id
    send_bill: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    send_ready: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    send_daily: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    send_cash: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)  # cash difference to managers
    daily_to: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)       # owner/manager mobiles
    send_alerts: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    alert_discount_bp: Mapped[int] = mapped_column(Integer, default=2000, server_default="2000", nullable=False)  # 20%
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class OwnerAlert(Base):
    """Something the owner should know at once: shown in Wise (Home) and sent by SMS when that's on."""
    __tablename__ = "owner_alerts"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    outlet_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("outlets.id"))
    kind: Mapped[str] = mapped_column(String(16), nullable=False)   # cancel | discount | comp | edit | cash | printer | box
    text: Mapped[str] = mapped_column(String(300), nullable=False)
    ref: Mapped[str | None] = mapped_column(String(80))
    seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    __table_args__ = (UniqueConstraint("tenant_id", "kind", "ref"),)


class SmsMessage(Base):
    """Outbox: every SMS is written here first, then sent by the SMS worker, with retries (so an
    internet outage on a Wise Box only delays messages)."""
    __tablename__ = "sms_messages"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    outlet_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("outlets.id"))
    kind: Mapped[str] = mapped_column(String(10), nullable=False)          # bill | ready | daily | test
    to_phone: Mapped[str] = mapped_column(String(10), nullable=False)
    body: Mapped[str] = mapped_column(String(500), nullable=False)
    vars: Mapped[dict] = mapped_column(JSONB, nullable=False)
    ref: Mapped[str | None] = mapped_column(String(120))                  # stops the same SMS going twice
    status: Mapped[str] = mapped_column(String(8), default="queued", nullable=False)  # queued | sent | failed
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_try_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(300))
    provider_ref: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        Index("uq_sms_ref", "tenant_id", "kind", "ref", unique=True, postgresql_where=text("ref is not null")),
        Index("ix_sms_queue", "status", "next_try_at"),
    )


class BillLine(Base):
    __tablename__ = "bill_lines"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    bill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bills.id", ondelete="CASCADE"), index=True, nullable=False)
    variant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("variants.id"), nullable=False)
    item_name: Mapped[str] = mapped_column(String(120), nullable=False)
    variant_name: Mapped[str] = mapped_column(String(60), nullable=False)
    addons_text: Mapped[str | None] = mapped_column(String(300))
    hsn_sac: Mapped[str | None] = mapped_column(String(8))
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    gross_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    discount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    taxable_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    cgst_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    sgst_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    igst_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    rate_bp: Mapped[int] = mapped_column(Integer, nullable=False)
    tax_inclusive: Mapped[bool] = mapped_column(Boolean, nullable=False)


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    bill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bills.id", ondelete="CASCADE"), index=True, nullable=False)
    mode: Mapped[str] = mapped_column(String(14), nullable=False)
    amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    reference: Mapped[str | None] = mapped_column(String(60))  # UPI ref / card last 4
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (
        CheckConstraint("mode in ('cash','upi','card','due','complimentary')", name="ck_payment_mode"),
        CheckConstraint("amount_paise >= 0", name="ck_payment_amount"),
    )


# ---------------------------------------------------------------- audit

class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[uuid.UUID] = pk()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    entity: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(64))
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


TENANT_TABLES = [
    "brands", "outlets", "users", "user_outlets", "tax_groups", "categories", "items", "variants",
    "addon_groups", "addons", "item_addon_groups", "outlet_variant_overrides", "audit_log",
    "prep_areas", "daily_counters", "orders", "kots", "order_items",
    "shifts", "cash_movements", "bills", "bill_lines", "bill_revisions", "payments", "order_requests",
    "sms_messages", "sms_settings", "cash_alerts", "counter_devices", "offline_syncs",
    "suppliers", "stock_items", "recipe_lines", "stock_levels", "stock_moves", "purchases", "stock_counts", "day_closes", "upi_requests", "payment_settings",
    "loyalty_settings", "customers", "loyalty_entries", "accounts_settings", "drawers", "role_permissions", "card_terminals", "card_requests", "ad_slides", "owner_alerts",
]
