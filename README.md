# Wise — restaurant POS SaaS (v1.0)

Multi-tenant, white-label restaurant POS.

## Going online on AWS

See **deploy/aws/AWS-SETUP.md**: one EC2 server plus RDS PostgreSQL in Mumbai, your own domain with automatic HTTPS, nightly encrypted backups, and the `wise-cloud` command for status, logs and updates. Every device then installs Wise as an app from the browser (top bar > Install app, or Setup > Screens).

## New in v1.0

**Silent bill printing.** The counter, print and kiosk start-up files (Setup > Screens) open Chrome with `--kiosk-printing`, so bills and KOTs print straight to the default printer with no dialog.

**Sold out today.** Tap "Sold out" on the kitchen screen, or turn on sold-out mode on the Orders screen, and tap an item. It greys out at the counter, disappears from QR and kiosk menus, and new orders for it are refused. It resets itself at the next business day. API: `GET/POST /menu/outlets/{id}/sold-out`.

**Split and move.** On an open order: Split moves picked items into a new order (T4 becomes T4/2) so each part gets its own bill. Move sends the whole table, or just some items, to another table; items join the order already there. The bill dialog also has "Split equally" for dividing one bill between people paying different ways. API: `POST /orders/{id}/split`, `POST /orders/{id}/move`.

**Training mode.** Outlets > "Make a training copy" creates a TRn outlet with the same menu and staff. Bills there print "TRAINING - NOT A BILL", use their own invoice series, never count in reports, accounts or loyalty, send no SMS, and UPI/card use the test provider. "Start fresh" wipes the practice data.

**Owner alerts.** The owner gets an alert (Home screen and SMS) for a cancelled bill, an edited bill, a complimentary bill, a discount at or above the threshold (default 20%, set in SMS settings), a KOT not printed for 2 minutes, and the box having been off. The box-off alert is sent when the box comes back on. Alerts are SMS only; WhatsApp is not included.

## New in v0.3

**Billing (Orders screen → Bill, or "Send & bill")**
- GST bill per order: inclusive or exclusive prices, CGST/SGST (or IGST) per line, round-off, tax summary by rate.
- Bill numbers `PT1/2627/000123`: one series per outlet per financial year, never reused, always 16 characters or less (GST Rule 46). Outlet codes are now 2-4 characters so the number always fits.
- Discount in % or ₹ with a reason, spread across lines before tax. Above the restaurant's limit (default 10%) a manager PIN is needed. Complimentary bills also need a manager PIN.
- Payments: cash (with change), UPI (QR with the exact amount on screen), card, credit (due, needs the customer's mobile) and split payments.
- Customer mobile asked on every bill, with a "Customer declined" option. Optional customer GSTIN for business bills.
- Prints an 80 mm bill through the browser (Bills → Reprint). The ESC/POS print bridge comes later.
- Cancel a bill: reason + manager PIN. The number stays in the series marked cancelled; the order can be billed again. Cancel an unbilled order: the KDS tickets disappear.

**Several people at once**
- Each cashier opens their own shift (opening cash, cash in/out, closing count with difference). Two or more cashiers can bill at the same outlet together; bill and token numbers stay in one clean series.
- Any number of waiters/captains can take orders at once. If two waiters punch the same table, the second one's items join that table's open order as a new KOT instead of creating a duplicate. A retried "send" never sends twice.

**QR ordering** (Outlets → tables + "Customers can order by scanning the table QR"; Kitchen setup → Print table QRs)
- Customer scans the table QR (or the takeaway QR), sees the outlet's menu with sizes and add-ons, enters name + mobile and orders.
- The order appears under "Waiting for you" on the Orders screen with a chime. Staff Accept (KOTs go to each preparation area; dine-in joins the table's open order) or Reject with a reason. Optional auto-accept.
- The customer's phone shows: waiting → confirmed with token → preparing → ready.

**Self-order kiosk** (Outlets → "Self-order kiosk"; Kitchen setup → Open kiosk; run the browser full screen on a touch monitor)
- Big tiles and cart, eat here / takeaway, name + mobile, total with GST, then **Pay by UPI** (QR with the exact amount and an order code) or **Pay at counter**.
- The cashier taps "UPI received" (or "Paid cash" / "Card") on the Orders screen. That makes the GST bill under their shift and sends the KOTs in one step; the kiosk shows the token number. It resets itself after 90 seconds idle.
- Automatic UPI confirmation needs a payment gateway account (Razorpay, PhonePe PG, Paytm). Plug it into `POST /api/orders/{id}/accept-paid` from the gateway's webhook; until then the cashier confirms.
- **Customer screen** for dual-screen POS: Orders > "Open customer screen" puts a second window on the customer-facing monitor. When idle it plays the restaurant's ads (Setup > Screen ads: pictures or MP4 videos, per outlet, with start/end dates). Adding an item switches it to the order; the number pad appears only when the cashier taps "Ask customer on their screen", and otherwise the screen ignores touches. The customer's number lands in the cashier's cart. Both windows talk inside the POS computer, so it works even when the box is down. Touch on the second monitor: Windows > Control Panel > Tablet PC Settings > Setup, then tap the customer screen when asked.
- At the counter, UPI that confirms itself works with **Paytm Dynamic QR** or Razorpay (Setup > Payments): each bill gets its own QR with the amount filled in, shown on screen or printed on the bill. The tax invoice is made when Paytm says paid, and the counter pops up "Token 12 paid" even after the cashier has moved on.

**Reports** (owners/managers): net sales, bills, average bill, discounts, GST by rate, payment modes, order types, by outlet, **item-wise sales**, captain-wise and table-wise sales, cancellations with reason and approver. CSV download for GST and item-wise.

## New in v0.2: orders, KOT split by preparation area, kitchen display, customer screen

- **Preparation areas** (Kitchen setup): Shake counter, Kitchen, Ice cream, Pizza oven… Set each item's area in the Menu or in the Excel import's "Prep Area" column.
- **Orders** screen (counter / captain): dine-in with table, takeaway with car number (an outlet can make it required), delivery. Sizes, add-ons, kitchen note. "Send to kitchen" gives a **token number that restarts every day** (the day rolls over at 4 am, so late-night service keeps one series).
- **KOT split**: one order becomes one KOT per preparation area. A shake + fries order sends a Shake counter KOT and a Kitchen KOT. Items added later go out as a new KOT marked "Added items". Items with no area go to a default "Kitchen" KOT.
- **Kitchen display (KDS)**: pick the area (or All). Three columns: **New → Preparing → Ready**, then **Served / Handed over**. Tickets show token, order type, table or car number, items, add-ons, notes and a timer that turns amber at 8 min and red at 15 min. Undo for mis-taps, chime on new tickets, full-screen mode. Refreshes every 4 seconds.
- **Order status** follows its KOTs: an order is Ready only when every area's KOT is ready. The counter can't hand over an order while any area is still preparing (an owner/manager can force it).
- **Customer order-ready screen**: Kitchen setup shows a secret link per outlet (`/display/<key>`). Open it on the counter TV: Preparing and Ready token numbers, with car numbers for takeaway and a chime and flash when an order becomes ready. No login; "New link" invalidates the old one.
- **Find an order** by token or the last 4 digits of the car number.
- Roles: kitchen staff move tickets; cashiers/captains take orders and mark them served; nobody else's outlet is visible.

Billing (payments, GST invoice numbers, printing) is next and builds on these orders.

## Phase 1 foundations

- **Restaurants (tenants)** sign up with owner email + mobile (OTP), pick a login ID (`shakewallah`), and get a 14-day trial.
- **White-label branding**: logo, colour and app name shown on the login screen, the app, the browser tab and the installed app icon (per-restaurant PWA manifest). Brands inside a restaurant (Shakewallah, Icecreamwallah) each get their own logo.
- **Outlets** with GSTIN, FSSAI, state code, bill footer, invoice-prefix code, car-number-required setting and stock-check frequency.
- **People and roles**: owner, manager, cashier, captain, kitchen. Owners/managers sign in with email/mobile OTP; staff use a 4-digit PIN on the outlet's device (locks after 5 wrong tries for 10 minutes).
- **Menu**: categories, items with variants (300 ML / 1000 ML as separate SKUs), add-on groups, tax groups (GST 5% default, inclusive or exclusive), short codes, veg/non-veg/egg, kitchen station.
- **Outlet overrides**: switch items off or change prices per outlet without touching the brand menu.
- **Excel import**: download a template, check the file (row-by-row errors), then import. Re-importing updates by name; nothing is deleted.
- **Data isolation** with Postgres row-level security on every tenant table, plus an audit log of every change.

Money is always integer paise. Tax rates are basis points (2.5% = 250).

## Run it

Needs Docker, Python 3.12+ and Node 20+.

```bash
# 1. Database + API (migrations run automatically)
docker compose up -d --build
curl http://localhost:8000/api/health          # {"ok": true}

# 2. Frontend
cd frontend
npm install
npm run dev                                    # http://localhost:5173
```

Open http://localhost:5173 → **Create your account**. In local mode (`WISE_OTP_PROVIDER=console`) the OTP is shown on screen and printed in the API log (`docker compose logs api`), so no SMS account is needed.

API docs (Swagger): http://localhost:8000/docs

### Without Docker for the API

```bash
docker compose up -d db
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload
```

## Tests

```bash
cd backend
pytest                                          # unit tests: GST maths, login IDs, phones, PIN/OTP/JWT, permissions, Excel parsing
docker compose up -d db
WISE_RUN_API_TESTS=1 pytest tests/test_api.py   # end-to-end: signup, OTP, PIN, menu, overrides, import, cross-tenant isolation
```

`test_api.py` wipes and re-creates the schema in the database it points at. Never run it against production.

```bash
cd frontend && npm run typecheck
```

## Status of this code

Written without being able to install packages or run Postgres in the build environment, so:

- **Run and verified:** 97 backend unit tests pass (GST bill maths, discount spreading, invoice numbering, payments, KOT split, status flow, token day roll-over, car numbers); mypy is clean; the frontend bundles, and every page was rendered in headless Chrome against a mocked API with no runtime errors.
- **Not yet run:** the FastAPI app against a real database, the Alembic migration, `test_api.py` (now also covering billing, two cashiers, same-table merge, manager PIN, cancellations, QR and kiosk orders, reports), `tsc` type-checking, and the `qrcode` package (QR images were stubbed in the browser check). Run those first (commands above) and fix anything they flag before building Phase 2.

## Going to production (checklist)

- Before the first real deployment, replace `alembic/versions/0001_initial.py` (which builds tables from the current models) with explicit operations: on an empty database run `alembic revision --autogenerate -m initial`, keep the RLS section, and from then on add one migration per change.
- KDS and the customer screen refresh by polling (every 3-4 s). That's fine for a few outlets; switch to WebSockets/SSE as you add restaurants. They also need internet today; the local Wi-Fi hub for offline service is in the spec.
- Set `WISE_JWT_SECRET` to a random 48+ character string (the API refuses to start in non-console OTP mode with a weak secret).
- Change the `wise_owner` / `wise_app` database passwords in `infra/postgres-init/01-roles.sql`. The API must connect as `wise_app` (not the table owner) or row-level security is bypassed.
- `WISE_OTP_PROVIDER=msg91` with `WISE_MSG91_AUTH_KEY` and a DLT-approved `WISE_MSG91_TEMPLATE_ID`. Email OTP delivery is a TODO (currently logged only).
- Set `WISE_BASE_DOMAIN` (e.g. `wise.in`) and a wildcard DNS record + TLS certificate for `*.wise.in` so each restaurant gets `<login_id>.wise.in`.
- Move uploads from the local `uploads/` folder to S3 (ap-south-1).
- Add rate limiting at the proxy (nginx/Cloudflare) for `/api/auth/*`.

## Layout

```
backend/
  app/core/        pure logic, unit-tested (tax, login IDs, contact, security, permissions, Excel import, menu resolve)
  app/routers/     auth, public branding, tenant/brands/outlets, users, menu, menu import
  app/services/    OTP delivery, PIN throttling, audit log
  app/models.py    SQLAlchemy models
  alembic/         migration 0001 creates tables + RLS policies
frontend/
  src/session.tsx  restaurant detection (?r=, saved ID, subdomain), branding, sign-in state
  src/pages/       Setup, Login (OTP + PIN pad), Signup, Home, Branding, Outlets, Staff, Menu, Import, Outlet menu, Tax
infra/postgres-init/  database roles
```

## Next

Inventory and recipes (stock deducted from every bill), print bridge for ESC/POS KOT and bill printers per area, table floor plan, payment gateway for automatic UPI confirmation, day-end close, offline queue + `/sync`, Zomato/Swiggy (version 2), WhatsApp marketing (version 2).

## Wise Box (own server in the restaurant)

`box/` runs Wise on one small computer in the restaurant: billing keeps working on the shop Wi-Fi when the
internet is down, the built-in print bridge prints KOTs, and every backup is encrypted and copied to the
box, a USB drive, Google Drive and a second cloud store. Setup steps: [box/BOX-SETUP.md](box/BOX-SETUP.md).
Day-to-day: `sudo wise status`.
