# Putting Wise online on AWS (Mumbai)

This puts Wise at your own address, for example **https://app.wisepos.in**, with HTTPS. Every
counter, phone, kitchen screen and TV then opens that address, and can install it as an app.

About 1–2 hours the first time. You need: a card for AWS, a domain, and a laptop.

## What you'll end up with

| Part | AWS service | Size to start | Cost (approx.) |
|---|---|---|---|
| App server | EC2, Ubuntu 24.04, ARM | t4g.small (2 GB) | ~₹720/month |
| Database | RDS PostgreSQL 16 (or Postgres on the same server) | db.t4g.micro | ~₹1,300/month (₹0 on the same server) |
| Fixed address | Elastic IP | 1 | ~₹300/month (AWS charges for every public IPv4) |
| Disk and backups | EBS 30 GB + RDS backups | | ~₹300/month |

Move to t4g.medium and db.t4g.small (about ₹4,600/month in total) once you pass 20–30 stores or
bills feel slow. Changing size takes a few minutes; do it at night.

## 1. AWS account (10 minutes)

New AWS accounts get up to $200 in credits. Pick the **Free plan** only for testing: it closes the
account after 6 months or when the credits run out. For real billing pick the **Paid plan**; the
credits still apply and nothing shuts down.


1. Create an account at aws.amazon.com. Turn on **MFA** for the root user (Security credentials).
2. Top right, choose the region **Asia Pacific (Mumbai) ap-south-1**. Do everything below in Mumbai.
3. Billing > **Budgets** > Create budget > Monthly cost budget, ₹3,000, email alert at 80%.
   You'll hear about any surprise before it gets big.

## 2. Database: RDS (15 minutes, then 10 minutes of waiting)

RDS > **Create database**:

- Standard create, **PostgreSQL**, version **16**.
- Template: **Free tier** (first year) or Production later.
- DB instance identifier: `wise-db`. Master username: `postgres`. Choose a strong master password and
  keep it safe (password manager). Wise makes its own users; you need the master password only once.
- Instance: **db.t4g.micro**. Storage: gp3, 20 GB, storage autoscaling on.
- Connectivity: **Public access: No**. VPC security group: **Create new**, name `wise-db`.
- Backups: automated backups on, retention as long as it allows (the free tier caps it; raise it to 14 days later).
- Encryption **on**. **Deletion protection on.**

When it says *Available*, open it and copy the **Endpoint** (ends in `.rds.amazonaws.com`).

## 3. App server: EC2 (10 minutes)

EC2 > **Launch instance**:

- Name `wise-app`. Image: **Ubuntu Server 24.04 LTS**, architecture **64-bit (Arm)**.
- Instance type **t4g.small**.
- Key pair: create one, `wise-key`, download the `.pem` file and keep it safe.
- Network: Create security group `wise-web` allowing
  **SSH from My IP**, **HTTP from anywhere**, **HTTPS from anywhere**.
- Storage: **30 GB gp3**.

Then:

1. EC2 > **Elastic IPs** > Allocate > Associate it with `wise-app`. Note the address (e.g. `13.233.x.x`).
   In Cloudflare, add the A record with the **grey cloud (DNS only)**, TTL Auto.
2. Let the server reach the database: EC2 > Security Groups > `wise-db` > Edit inbound rules > Add
   **PostgreSQL (5432)**, source: the `wise-web` security group.

## 4. Domain (5 minutes, then up to an hour for it to spread)

Buy a domain (a `.in` is ₹700–1,200 a year) from any registrar. In its DNS settings add:

| Type | Name | Value |
|---|---|---|
| A | `app` | your Elastic IP |

Check before going on, from your laptop: `nslookup app.wisepos.in` should show the Elastic IP.

## 5. Install Wise on the server (20 minutes)

From your laptop (Windows: use PowerShell), in the folder with the key and the Wise zip:

```
scp -i wise-key.pem wise-v1.1.0.zip ubuntu@13.233.x.x:
ssh -i wise-key.pem ubuntu@13.233.x.x
```

On the server:

```
sudo apt-get update && sudo apt-get install -y unzip
unzip wise-v1.1.0.zip
sudo bash wise/deploy/aws/install.sh
```

It asks for:

1. **Your Wise address**: `app.wisepos.in`
2. **Email** for certificate notices
3. **Database**: choose `1` (RDS), then paste the RDS endpoint, `postgres`, and the master password.

At the end it shows a **backup key**. Save it in two places (password manager and a printed copy at
home). Without it nobody, not even you, can open a backup.

## 6. First sign-in

1. Open **https://app.wisepos.in** and create the restaurant account (Shakewallah).
2. Sign-in codes: until SMS is set up, run `sudo wise-cloud login-code` on the server after tapping
   "Send code".
3. Once your own restaurant is in, sign-up closes by itself (`first` mode). When you start selling:
   `sudo wise-cloud signup open`.
4. SMS codes (MSG91): `sudo wise-cloud sms <auth-key> <template-id>`.

## 7. Install it as an app on every device

Open https://app.wisepos.in on the device and sign in once, then:

- **Android phone or tablet**: Chrome > ⋮ > **Install app**, or tap **Install app** in Wise's top bar.
- **Windows counter PC**: Chrome/Edge > the install icon at the right end of the address bar, or
  **Install app** in Wise's top bar. For a counter that should start by itself in the morning, use the
  start-up file from **Setup > Screens** instead; it also prints bills without a dialog.
- **iPhone or iPad**: Safari > Share > **Add to Home Screen**.
- **Kitchen screens and TVs**: use the links in Setup > Screens (no sign-in needed).

The installed app updates itself whenever the server is updated.

## 8. KOT printers in the shop

Network (LAN / Wi-Fi) KOT printers sit in the shop, so a small program on any always-on PC in the
shop sends them the KOTs. On that PC: install Python, copy the `print-bridge` folder, edit
`start-wise-print.bat` to `--server https://app.wisepos.in --key <print key>` (Setup > Kitchen setup > Copy print key), and put it
in the Startup folder (Win+R, `shell:startup`).

## Looking after it

| What | Command (on the server) |
|---|---|
| Is everything running? | `sudo wise-cloud status` |
| Logs | `sudo wise-cloud logs api` |
| Backup now | `sudo wise-cloud backup` |
| Install a new version | copy the zip up, then `sudo wise-cloud update wise-v1.2.0.zip` |
| Restart | `sudo wise-cloud restart` |

**Backups**: RDS keeps automatic daily backups (7–14 days, restore to any minute). On top of that the
server makes an encrypted backup every night at 4:20 am and keeps 14 days. To also copy those to S3:
create a bucket, give the EC2 server an IAM role that may write to it, then
`sudo wise-cloud s3 <bucket-name>`.

**Security updates** for Ubuntu install by themselves. Keep SSH open only to your own IP.

## Important: the shop needs internet

In this cloud setup every counter talks to the server over the internet. If the shop's internet
goes down, billing stops until it's back. Until offline billing is built, keep a **4G/5G hotspot or a
second broadband line** at each counter as a backup. (The Wise Box setup in `box/` doesn't have this
limit, because the box is inside the shop.)
