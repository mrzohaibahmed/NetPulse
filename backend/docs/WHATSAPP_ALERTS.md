# WhatsApp Alerts Setup

NetPulse can send **critical device offline** and **device recovery** notifications through the official [Meta WhatsApp Cloud API](https://developers.facebook.com/docs/whatsapp/cloud-api). WhatsApp is an **additional** notification channel alongside email — monitoring, alert creation, and email delivery are unchanged.

WhatsApp is **disabled by default** (`WHATSAPP_ALERTS_ENABLED=false`).

## Prerequisites

1. A [Meta Developer](https://developers.facebook.com/) account.
2. A Meta app with the **WhatsApp** product added.
3. A WhatsApp Business account linked to the app.
4. A phone number registered for the Cloud API (test or production).

## Setup steps

### 1. Create a Meta Developer application

1. Go to [Meta for Developers](https://developers.facebook.com/).
2. Create an app (type: **Business** is typical for WhatsApp).
3. Add the **WhatsApp** product to the app.

### 2. Configure WhatsApp Business Platform / Cloud API

1. In the app dashboard, open **WhatsApp → API Setup**.
2. Note the **Phone number ID** (not the display phone number).
3. Note the **WhatsApp Business Account ID** (optional for sending; useful for admin reference).
4. Generate a **temporary** or **permanent** access token with `whatsapp_business_messaging` permission.

### 3. Create and approve message templates

Proactive alerts must use **approved template messages**. Create these in [Meta Business Manager](https://business.facebook.com/) → **WhatsApp Manager** → **Message templates**.

#### Critical offline template (`netpulse_critical_alert`)

Suggested body (5 variables):

```text
🚨 NetPulse Critical Device Alert

Device: {{1}}
IP: {{2}}
Status: {{3}}
Severity: {{4}}
Time: {{5}}
```

#### Recovery template (`netpulse_device_recovery`)

Suggested body (4 variables):

```text
✅ NetPulse Device Recovery

Device: {{1}}
IP: {{2}}
Status: {{3}}
Time: {{4}}
```

Template names must match your `.env` values:

- `WHATSAPP_CRITICAL_ALERT_TEMPLATE`
- `WHATSAPP_RECOVERY_ALERT_TEMPLATE`

Use language code `en` (or set `WHATSAPP_TEMPLATE_LANGUAGE` to match your template).

### 4. Configure NetPulse

Add to `backend/.env` (see `.env.example`):

```env
WHATSAPP_ALERTS_ENABLED=true

WHATSAPP_ACCESS_TOKEN=your_meta_access_token
WHATSAPP_PHONE_NUMBER_ID=your_phone_number_id
WHATSAPP_BUSINESS_ACCOUNT_ID=your_business_account_id
WHATSAPP_API_VERSION=v21.0

WHATSAPP_RECIPIENT_NUMBERS=923001234567,923111234567

WHATSAPP_CRITICAL_ALERT_TEMPLATE=netpulse_critical_alert
WHATSAPP_RECOVERY_ALERT_TEMPLATE=netpulse_device_recovery
WHATSAPP_TEMPLATE_LANGUAGE=en
WHATSAPP_REQUEST_TIMEOUT_SECONDS=10
WHATSAPP_CRITICAL_ALERTS_ENABLED=true
WHATSAPP_RECOVERY_ALERTS_ENABLED=true
```

**Security:** Never commit real tokens. The access token is backend-only and is not exposed through REST APIs or the frontend.

Recipient numbers are E.164 without `+` (country code + number, comma-separated for multiple recipients).

### 5. Restart NetPulse

Restart the backend after changing `.env` so configuration is reloaded.

### 6. Test the integration

As an admin, call:

```http
POST /api/settings/test-whatsapp
Authorization: Bearer <token>
```

This sends a test message using the critical alert template and sample values. It does **not** run automatically on startup.

## When WhatsApp is sent

| Event | Trigger | Channel |
|-------|---------|---------|
| Critical device offline | Existing `maybe_send_critical_offline_alert` after alert insert | Email (unchanged) + WhatsApp |
| Critical device recovery | Existing `resolve_critical_offline_alerts` when alerts are resolved | WhatsApp only (no recovery email today) |

WhatsApp is **not** sent on every failed ping — only when NetPulse’s existing offline confirmation and critical alert logic fires.

Duplicate WhatsApp messages are prevented by the same deduplication that guards alert creation (active alert check + unique index).

## Failure behavior

If the WhatsApp API fails (timeout, invalid token, Meta outage):

- The failure is logged (`[WHATSAPP]` prefix).
- Monitoring, alert creation, and email continue normally.
- NetPulse does not crash.

## Disabling WhatsApp

```env
WHATSAPP_ALERTS_ENABLED=false
```

No WhatsApp API calls are made. Email and monitoring are unaffected.

You can also disable individual alert types:

```env
WHATSAPP_CRITICAL_ALERTS_ENABLED=false
WHATSAPP_RECOVERY_ALERTS_ENABLED=false
```
