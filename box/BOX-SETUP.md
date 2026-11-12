# Wise Box setup

One small computer in the restaurant runs Wise for everyone there: billing counters, captain phones,
kitchen screens, the TV, the kiosk and the KOT printers. Billing keeps working on the shop Wi-Fi even
when the internet is down. Every backup is encrypted and kept in four places: the box itself, a USB
drive plugged into it, Google Drive, and a second cloud store.

## What to buy

| Item | Notes |
|---|---|
| Mini PC (Lenovo ThinkCentre Tiny, HP ProDesk Mini, Dell OptiPlex Micro, Intel NUC) | Intel i5 or Ryzen 5, 16 GB RAM, 512 GB NVMe SSD, wired network port |
| USB SSD, 256 GB or more | the local backup drive; stays plugged in |
| UPS (600–1000 VA) | for the box **and** the Wi-Fi router |
| Good Wi-Fi router or access point | captain phones and kitchen screens depend on it |
| Network (LAN/Wi-Fi) thermal printers, 80 mm | one per preparation area |

Plug the box into the router with a cable, not Wi-Fi.

### Using a laptop as the box

A laptop works (for example a Core i7 laptop with 8–16 GB RAM and an SSD). Install on the SSD. The
installer sets it to keep running with the lid closed and, if the laptop allows it, to stop charging
at 80% so the battery doesn't swell from being plugged in all the time. Its battery acts as a UPS:
you get an alert when the power goes off, and the box shuts down safely at 12% battery. **Most
laptops don't switch themselves back on when power returns**, so after a long power cut someone has
to press the power button. Keep it on a hard surface with the vents free, away from kitchen heat.

## Install (about 45 minutes, once)

1. **BIOS setting.** Switch the PC on and press F1/F2/F10/Del for setup. Set **"After power loss: Power on"**
   (it may be called "AC recovery" or "Restore on AC power loss"). The box then starts by itself after a
   power cut.
2. **Install Ubuntu Server 24.04 LTS** from a USB stick (ubuntu.com/download/server). Choose "Use an
   entire disk", tick **Install OpenSSH server**, and create a user (e.g. `wise`). Note the password.
3. **Copy Wise to the box** (USB stick, or download it), then run:

   ```
   unzip wise-0.6.0.zip
   sudo bash wise/box/install.sh
   ```

   It installs everything, starts Wise, and at the end shows your **backup key**. Save the key in two
   places (password manager and a printed copy at home). **Without the key no backup can be opened, by
   anyone.**
4. **Router:** reserve the box's address (shown at the end, e.g. `192.168.1.20`) in the router's DHCP
   settings, so it never changes.
5. **Open Wise** on any device on the shop Wi-Fi: `http://192.168.1.20`. Create the restaurant account.
   For the sign-in code, run `sudo wise login-code` on the box. After this first restaurant, sign-up
   closes on the box.

## The four backup copies

Backups run by themselves at 4:20 am and 3:20 pm. Each one is checked by restoring it into a scratch
database every Monday morning.

1. **On the box**: 14 daily, 8 weekly and 12 monthly backups.
2. **USB drive**: plug it into the box and run `sudo wise usb-prepare` once (it erases the drive).
   Leave it plugged in.
3. **Google Drive**: connect it once:
   ```
   sudo rclone config --config /opt/wise/config/rclone.conf
   ```
   Choose `n` (new remote), name it `gdrive`, storage `drive`, leave client id/secret empty, scope `1`
   (full access), and answer **No** to "Use web browser to automatically authenticate". It then shows a
   command; run that command on your laptop (install rclone from rclone.org), sign in to Google there, and
   paste the result back. Then:
   ```
   sudo wise offsite add gdrive:WiseBackups
   ```
   Use a Google account with free space (each backup is small: a few MB per month of sales).
4. **Second cloud**: Backblaze B2, E2E Object Storage, AWS S3 or another S3-compatible store. Add it
   the same way with `rclone config` (type `s3` or `b2`), then `sudo wise offsite add b2:wise-shakewallah`.

Copies only ever add files, so a damaged or wiped box can't wipe the copies. Old copies are cleared by
age (daily after 30 days, weekly after 90, monthly after 400).

## Public address (QR ordering and access from home)

Customers scanning a table QR use mobile data, so they reach the box through a Cloudflare Tunnel:
free, no open ports on the router, no static IP needed.

1. Add your domain to a free Cloudflare account (cloudflare.com).
2. Zero Trust > Networks > Tunnels > Create tunnel > Cloudflared. Copy the **token**.
3. In the tunnel, add a public hostname such as `pos.shakewallah.in`, service `HTTP`, URL `web:80`.
4. On the box: `sudo wise tunnel <token> https://pos.shakewallah.in`
5. Print the table QR codes from **Kitchen setup**. They now use the public address.

## UPI that confirms itself (Razorpay)

1. Open a Razorpay account in the restaurant's name and ask Razorpay to switch on **UPI QR Codes**.
2. In Wise: Setup > Payments > Razorpay. Paste the Key ID and Key Secret.
3. In Razorpay: Webhooks > Add. Use the URL shown in Wise, tick the `qr_code.credited` event, set a secret, and
   paste the same secret in Wise. The webhook needs the box's public address (`sudo wise tunnel`).
4. Without the webhook it still works: while a QR is on the counter screen, the box asks Razorpay every
   3 seconds. That needs the box to have internet.
5. If the internet is down, the counter falls back to the outlet's own UPI QR and the cashier taps UPI received.

Try it first in **Test** mode (no real money): the counter shows a "Customer paid (test)" button.

## Card machines that fill in the amount (Razorpay POS)

1. Ask Razorpay POS for an Android Smart POS per billing counter, and for "P2P / push to POS" API access for
   your own billing software. They give a username, an app key and each machine's device id.
2. Setup > Payments > Card machines: switch on, pick Razorpay POS, paste the username and app key, and add each
   machine with its device id. Keep "Use Razorpay's test server" ticked until a test card payment works.
3. Each billing counter is asked once which machine is next to it. Tap Card: the amount appears on that
   machine, the customer taps the card, and the bill prints with the card's last 4 digits and approval code.
4. The box needs internet for this (the request goes through Razorpay's server to the machine). Without it,
   type the amount on the machine and tap Card received.

## Printers

First decide which items go to which printer: **Kitchen setup > Which items print where**. Set a
whole category at once (all shakes to the shake counter) or open a category to change single items.
Then give each network printer a fixed address (printer menu or router), enter it under **Kitchen setup >
preparation area > Printer**, switch on **Print KOTs**, copy the **print key**, then on the box:
`sudo wise print-key <key>`. The box itself prints every KOT; no other PC is needed.

## Alerts

`sudo wise alerts email you@example.com` (it asks for an SMTP server; for Gmail use an app password
and `smtps://smtp.gmail.com:465`). Or `sudo wise alerts webhook <url>` for a messaging service.
`sudo wise alerts test` sends a test. You're told once when something goes wrong (box not answering, disk
filling up or failing, no backup in 26 hours, USB drive unplugged, a cloud copy failing, public address
down, clock wrong) and once when it's fixed.

## Rush-hour test (before you go live, and after big changes)

After closing time, run `sudo wise loadtest`. It starts a throwaway copy of Wise on the box, plays
three times your busiest hour against it for 5 minutes (15 captains, 6 counters, QR phones, kiosk,
kitchen screens, TV, printing), then prints a report: how fast each kind of tap was (95% of bills and
orders must take under half a second, screens under 0.3 s, a KOT must reach its printer within 3 s)
and whether the books are right (no gaps or duplicates in bill numbers, every KOT printed once,
payments equal totals). The copy is deleted afterwards; your real data isn't touched. Different length
or load: `sudo wise loadtest 10 5` (10 minutes, 5 times peak). Reports are kept in `/opt/wise/state/`.

## Everyday commands

| | |
|---|---|
| `sudo wise status` | is everything working? addresses, backups, copies |
| `sudo wise backup` | back up right now |
| `sudo wise update wise-0.7.0.zip` | new version; backs up first and goes back by itself if it fails. Do it after closing time. |
| `sudo wise restart` | if something seems stuck |
| `sudo wise logs api` | details for whoever is helping you |

## If the box dies

1. Set up any computer with steps 1–3 above (a laptop works for a day or two).
2. Copy the latest backup from the USB drive (`wise/daily/…`) or from Google Drive.
3. Put your backup key in a file, e.g. `key.txt`, then:
   ```
   sudo wise restore wise-20261110-0420.tar.age --key key.txt
   ```
4. Reserve the new box's address in the router, or give it the old one.

Everything comes back: menus, bills, staff, settings. Devices keep their addresses if the box keeps
its IP. You lose at most what was billed after the last backup, so a midday backup runs as well.

## When the box itself is down (or the Wi-Fi to it)

A billing counter that was open on the Orders screen keeps billing on its own:

- A red bar says "Box not reachable". Cash, UPI and card bills still work, with the same GST maths
  as the box. The bill and one KOT slip per kitchen area print on the counter's printer. Hand the slips
  to the kitchen; the kitchen screen can't show them while the box is down.
- Each counter has its own bill series (for outlet PT1: PT1A/2627/000001, the next counter PT1B…) and
  tokens from 1001 (second counter 2001), so nothing clashes with the box's numbers. The GST
  "Invoice number series" report lists each series separately for GSTR-1.
- Bills wait on that counter until the box answers, then upload by themselves within a few seconds.
  If one can't be added (for example the cashier's shift isn't open), the bar says why; fix it and tap
  Try again. A shift can't be closed until its offline bills have uploaded.
- Not available offline: discounts, split or credit bills, pay later, captains sending orders,
  QR/kiosk orders, the kitchen screen.
- Keep that browser tab open. The counter talks to the box over plain http on the shop network, so
  the browser can't keep the app for a reload. A counter that is reloaded or switched on while the
  box is down can't open Wise; use paper bills and enter them later.

## When the internet is down

Billing, KOTs, kitchen screens, the TV and printers keep working on the shop Wi-Fi. QR orders from
customers' phones, UPI confirmation from the payment app, owner sign-in by SMS and access from home
wait until the internet is back.
