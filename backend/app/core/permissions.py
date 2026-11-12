"""Role -> permission mapping (spec: Roles, permissions & audit)."""
from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    owner = "owner"
    manager = "manager"
    cashier = "cashier"
    captain = "captain"
    kitchen = "kitchen"


class Perm(str, Enum):
    tenant_settings = "tenant.settings"      # branding, tax setup, brands
    outlets_manage = "outlets.manage"
    users_manage = "users.manage"
    menu_manage = "menu.manage"              # master menu, tax groups, import
    menu_override = "menu.override"          # outlet price/availability overrides
    menu_view = "menu.view"
    reports_view = "reports.view"
    costing_view = "costing.view"
    billing = "billing"
    kot_send = "kot.send"
    kds_view = "kds.view"
    kds_update = "kds.update"                # mark tickets preparing / ready
    order_complete = "order.complete"        # mark served / picked up
    stock_count = "stock.count"              # enter closing stock counts
    inventory_manage = "inventory.manage"    # purchases, recipes, prep, wastage, stock items, suppliers
    day_close = "day.close"                  # close the business day
    loyalty_manage = "loyalty.manage"        # loyalty on/off and rules, customer list
    approve = "approve"                      # approve discounts over the limit, cancellations, item corrections
    cash_expected = "cash.expected"          # see what a drawer should hold, and every cashier's shift
    ads_manage = "ads.manage"                # pictures and videos on the customer screen


ALL = set(Perm)

ROLE_PERMS: dict[Role, set[Perm]] = {
    Role.owner: ALL,
    Role.manager: {
        Perm.menu_override, Perm.menu_view, Perm.reports_view, Perm.costing_view,
        Perm.billing, Perm.kot_send, Perm.kds_view, Perm.kds_update, Perm.order_complete, Perm.stock_count,
        Perm.users_manage, Perm.inventory_manage, Perm.day_close, Perm.loyalty_manage, Perm.approve, Perm.cash_expected,
        Perm.ads_manage,
    },
    Role.cashier: {Perm.menu_view, Perm.billing, Perm.kot_send, Perm.kds_view, Perm.order_complete, Perm.stock_count},
    Role.captain: {Perm.menu_view, Perm.kot_send, Perm.kds_view, Perm.order_complete},
    Role.kitchen: {Perm.kds_view, Perm.kds_update, Perm.order_complete, Perm.stock_count, Perm.menu_view},
}

# Roles that log in with email/mobile OTP (remote access). Others use PIN on outlet devices.
OTP_ROLES = {Role.owner, Role.manager}

# Which roles a given role may create or edit.
ASSIGNABLE: dict[Role, set[Role]] = {
    Role.owner: {Role.owner, Role.manager, Role.cashier, Role.captain, Role.kitchen},
    Role.manager: {Role.cashier, Role.captain, Role.kitchen},
}


# What each permission means, in the owner's words, for the "Who can do what" screen.
# (group, label, help, sensitive: worth a second thought before giving it to counter staff)
CATALOG: dict[Perm, tuple[str, str, str, bool]] = {
    Perm.kot_send: ("Orders", "Take orders and send them to the kitchen", "Counter and captain screens.", False),
    Perm.billing: ("Orders", "Make bills and run a cash shift", "Bills, cash in / out, closing the shift, Bills list.", False),
    Perm.order_complete: ("Orders", "Mark orders served / handed over", "", False),
    Perm.approve: ("Orders", "Approve without a manager PIN", "Discounts over the limit, complimentary bills, cancellations and item corrections. Their PIN also approves for others.", True),
    Perm.kds_view: ("Kitchen", "See the kitchen display", "", False),
    Perm.kds_update: ("Kitchen", "Move tickets on the kitchen display", "Preparing, ready.", False),
    Perm.cash_expected: ("Cash", "See expected cash and every cashier's shift", "Without it a cashier counts blind: they never see what the drawer should hold.", True),
    Perm.day_close: ("Cash", "Close the day, manage drawers", "Day close, reopening a day, adding drawers.", True),
    Perm.reports_view: ("Reports", "See reports and accounts", "Sales, GST, cash, Tally and Excel files.", True),
    Perm.costing_view: ("Reports", "See costs and margins", "Purchase prices, recipe cost, food cost %, stock value.", True),
    Perm.stock_count: ("Stock", "Enter closing stock counts", "Blind: they don't see expected stock.", False),
    Perm.inventory_manage: ("Stock", "Manage stock", "Purchases, recipes, prep, wastage, stock items, suppliers.", True),
    Perm.loyalty_manage: ("Customers", "Customers and loyalty rules", "Customer list with spend, adjusting points.", True),
    Perm.ads_manage: ("Customers", "Ads on the customer screen", "Upload, schedule and remove the pictures and videos shown when the counter is idle.", False),
    Perm.menu_view: ("Menu", "See the menu", "", False),
    Perm.menu_override: ("Menu", "Change this outlet's prices and switch items off", "Outlet menu.", True),
    Perm.menu_manage: ("Menu", "Edit the menu, tax and kitchen setup", "Items, prices for all outlets, GST groups, printers.", True),
    Perm.users_manage: ("Setup", "Add and edit staff", "Can't create owners or change their own role.", True),
    Perm.outlets_manage: ("Setup", "Add and edit outlets", "", True),
    Perm.tenant_settings: ("Setup", "Restaurant settings", "Branding, SMS, payments (Razorpay keys), Tally names.", True),
}
EDITABLE_ROLES = (Role.manager, Role.cashier, Role.captain, Role.kitchen)


def has_perm(role: Role | str, perm: Perm) -> bool:
    return perm in ROLE_PERMS.get(Role(role), set())


def can_assign(actor: Role | str, target: Role | str) -> bool:
    return Role(target) in ASSIGNABLE.get(Role(actor), set())
