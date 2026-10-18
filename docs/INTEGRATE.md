# Integrating the HayOps Widget

This guide covers everything needed to embed the HayOps support widget into a new application. The widget provides a floating support button that lets authenticated users submit tickets with annotated screenshots and track their existing tickets — all without leaving your app.

---

## Overview

The integration has two parts:

1. **HayOps setup** — create a `SourceApplication` record that issues credentials for the client app
2. **Client app setup** — embed a `<script>` tag that loads the widget and passes user identity

The widget JS is hosted by the HayOps and versioned via its URL, so client apps receive cache-busted updates simply by updating the version number in their config.

---

## Step 1 — Create a SourceApplication

Each integrated application needs its own `SourceApplication` record. This controls:

- The public API key sent with every widget request
- The secret used to sign the user identity hash (HMAC)
- The list of domains allowed to make CORS requests to the API
- (Optional) A custom helpdesk email subdomain for inbound email

### Test environment

1. Log in to the **test** HayOps admin:
   `https://helpdesk-test.example.com/admin/`
2. Go to **Core → Source applications → Add**
3. Fill in:
   | Field | Value |
   |---|---|
   | Name | `<AppName> (Test)` |
   | Slug | `<appname>-test` |
   | Allowed domains | Your test domain(s), e.g. `["test.example.com", "localhost"]` |
   | Email domain | _(optional)_ e.g. `helpdesk.test.example.com` |
4. Save. The `api_key` and `api_secret` are auto-generated — copy them from the record.

### Production environment

1. Log in to the **production** HayOps admin:
   `https://helpdesk.example.com/admin/`
2. Go to **Core → Source applications → Add**
3. Fill in:
   | Field | Value |
   |---|---|
   | Name | `<AppName>` |
   | Slug | `<appname>` |
   | Allowed domains | Your production domain(s), e.g. `["example.com", "*.example.com"]` |
   | Email domain | _(optional)_ e.g. `helpdesk.example.com` |
4. Save and copy the `api_key` and `api_secret`.

> Keep the `api_secret` confidential — it is used server-side only and must never be exposed to the browser.

---

## Step 2 — Configure the client application

The client app needs four environment variables:

| Variable | Test value | Production value |
|---|---|---|
| `HELPDESK_URL` | `https://helpdesk-test.example.com` | `https://helpdesk.example.com` |
| `HELPDESK_API_KEY` | _(from test SourceApplication)_ | _(from prod SourceApplication)_ |
| `HELPDESK_API_SECRET` | _(from test SourceApplication)_ | _(from prod SourceApplication)_ |
| `HELPDESK_WIDGET_VERSION` | e.g. `1.2.5` | e.g. `1.2.5` |

The `HELPDESK_WIDGET_VERSION` controls which JS bundle is loaded. It must match a version that exists on the target HayOps instance — see [Widget versioning](#widget-versioning) below.

---

## Step 3 — Embed the widget

The widget is loaded by a single `<script>` tag. The tag must be rendered server-side so that:

- The user hash is computed from the **server-side secret** (never exposed to the browser)
- User identity (email, name) is injected from the authenticated session

### Django applications

The recommended pattern for Django apps uses a context processor to compute the user hash and inject all widget variables into the template context.

#### 1. Add the context processor

Create or extend a context processor that reads the helpdesk settings and computes the HMAC-SHA256 user hash:

```python
# config/context_processors.py
import hashlib
import hmac

from django.conf import settings


def helpdesk_widget(request):
    helpdesk_url = getattr(settings, "HELPDESK_URL", "")
    api_key = getattr(settings, "HELPDESK_API_KEY", "")
    api_secret = getattr(settings, "HELPDESK_API_SECRET", "")
    version = getattr(settings, "HELPDESK_WIDGET_VERSION", "1.0.0")

    if not helpdesk_url or not api_key:
        return {}

    user_hash = ""
    if request.user.is_authenticated and api_secret:
        user_hash = hmac.new(
            api_secret.encode(),
            request.user.email.encode(),
            hashlib.sha256,
        ).hexdigest()

    return {
        "helpdesk_widget_url": f"{helpdesk_url}/widget/v{version}/helpdesk-widget.js",
        "helpdesk_api_key": api_key,
        "helpdesk_api_url": f"{helpdesk_url}/api",
        "helpdesk_user_hash": user_hash,
    }
```

Register it in `settings.py`:

```python
TEMPLATES = [
    {
        ...
        "OPTIONS": {
            "context_processors": [
                ...
                "config.context_processors.helpdesk_widget",
            ],
        },
    },
]
```

Add the settings variables:

```python
HELPDESK_URL = env("HELPDESK_URL", default="")
HELPDESK_API_KEY = env("HELPDESK_API_KEY", default="")
HELPDESK_API_SECRET = env("HELPDESK_API_SECRET", default="")
HELPDESK_WIDGET_VERSION = env("HELPDESK_WIDGET_VERSION", default="1.0.0")
```

#### 2. Add the script tag to the base template

Add this block just before `</body>` in your base template. The `helpdesk_widget_url` guard means the widget is silently absent when the env vars are not configured (e.g. local dev without a helpdesk connection):

```html
{% if user.is_authenticated and helpdesk_widget_url %}
<script
  src="{{ helpdesk_widget_url }}"
  data-app-key="{{ helpdesk_api_key }}"
  data-api-url="{{ helpdesk_api_url }}"
  data-user-email="{{ user.email }}"
  data-user-name="{{ user.get_full_name }}"
  data-user-hash="{{ helpdesk_user_hash }}"
></script>
{% endif %}
```

#### 2b. Keeping clear of the corner

The launcher sits 20px from the bottom-right corner. When the host app already
has something there — a cookie bar, a sticky action bar, an intercom-style
button — pass an offset instead of fighting it. The panel moves with it:

```html
<script src="{{ helpdesk_widget_url }}" ... data-offset-bottom="88px" data-offset-right="24px"></script>
```

Values must be a plain CSS length (`px`, `rem`, `em`, `vh`, `vw`); anything else
is ignored and the default corner is used.

#### 3. Set environment variables

**.env (test)**
```
HELPDESK_URL=https://helpdesk-test.example.com
HELPDESK_API_KEY=<test api_key>
HELPDESK_API_SECRET=<test api_secret>
HELPDESK_WIDGET_VERSION=1.2.5
```

**.env (production)**
```
HELPDESK_URL=https://helpdesk.example.com
HELPDESK_API_KEY=<prod api_key>
HELPDESK_API_SECRET=<prod api_secret>
HELPDESK_WIDGET_VERSION=1.2.5
```

### Running without the secret (app-key-only)

`HELPDESK_API_SECRET` is optional. Leave it unset and the widget runs in
app-key-only mode: no HMAC, so the helpdesk treats every submission as
anonymous and the ticket list stays empty. In that mode the launcher opens
directly on the new-ticket form, and `data-user-email` / `data-user-name` are
used to prefill the form rather than to prove identity — the user can still
edit them, so the address on the ticket is self-asserted.

Use it when you want the widget live without distributing a secret to the app's
environment. Set the secret later and the same integration starts identifying
users and showing their ticket history, with no template change.

### Other frameworks

For non-Django apps, the same pattern applies — compute the HMAC server-side and render the script tag:

```
HMAC-SHA256(key=HELPDESK_API_SECRET, message=user_email) → hex digest
```

The script tag attributes are the same regardless of framework.

---

## Widget versioning

The widget JS is served at a versioned URL:

```
{HELPDESK_URL}/widget/v{VERSION}/helpdesk-widget.js
```

For example:
```
https://helpdesk.example.com/widget/v1.2.5/helpdesk-widget.js
```

The response carries `Cache-Control: public, max-age=31536000, immutable`, so browsers and CDNs cache it indefinitely.

The screenshot and annotation libraries it loads on demand (html2canvas-pro, markerjs2, pinned in `widget/package.json`) are served from the same place, `{HELPDESK_URL}/widget/v{VERSION}/vendor/`. A host page with a Content-Security-Policy therefore needs only the HayOps origin, in `script-src` and in `connect-src` (for the API), with `blob:` in `img-src` for the screenshot preview; it needs no CDN. The version in the URL acts as the cache buster — changing the version means a new URL, so a fresh download.

If that CSP has no `'unsafe-inline'` in `style-src` (1.2.5+): the widget's own styles use a constructed stylesheet, which CSP does not govern, so they need nothing. Two pieces do:

- Put the page's nonce on the widget tag (`<script src="..." nonce="{{ csp_nonce }}" ...>`). The annotator (markerjs2) injects a `<style>` and the widget gives it that nonce; without it, annotation fails and the ticket keeps the plain screenshot.
- Add `'sha256-UP0QZg7irvSMvOBz9mH2PIIE28+57UiavRfeVea0l3g='` to `style-src`. html2canvas-pro 1.5.11 injects that fixed rule into the page clone it screenshots, and the clone inherits the host's CSP. Without it a screenshot can draw `::before`/`::after` content twice.

**When the HayOps widget is updated:**

1. The HayOps `widget/package.json` version is bumped (e.g. `1.0.0` → `1.1.0`) and the bundle is rebuilt.
2. The old version URL (`/widget/v1.0.0/...`) returns 404, except the versions listed in `PREVIOUS_VERSIONS` (`apps/widget_api/widget_js.py`), which keep working for one release while client apps move.
3. Update `HELPDESK_WIDGET_VERSION` in each client app's environment to the new version.

**Test and production versions can differ** — it is normal to run a newer widget version in test while production stays on the previous version until verified.

---

## How the widget works

Once loaded, the widget:

- Renders a floating chat button in the bottom-right corner of the page
- Lets the user open a panel to view their existing tickets or create a new one
- On ticket creation, captures the current page URL and user agent automatically, plus a small diagnostic snapshot (see [Context capture](#context-capture) below)
- Offers a screenshot tool: captures the visible page, then opens an annotation overlay where the user can draw arrows, boxes, highlights, and callouts before attaching the image to the ticket
- Identifies the user via the HMAC hash — all tickets submitted through the widget are linked to their email address and visible in the HayOps agent view

The widget runs in a Shadow DOM, so its styles are fully isolated from the host application.

---

## Theming

The widget automatically matches your app's branding — no client-side config needed. It fetches `GET {HELPDESK_URL}/api/widget/config/?key={api_key}` on load and applies:

- `product_name` — shown in the panel header (falls back to generic copy if unset)
- `primary_color` — the launcher button, header, links, and form focus states all derive from this

Set `product_name` and `primary_color` on the `SourceApplication` record in the HayOps admin to customize.

---

## Context capture

Every ticket submitted through the widget automatically includes a `context` snapshot to help agents debug: viewport and screen size, timezone, referrer, platform, and the last 10 `console.error`/`window.onerror`/unhandled-rejection messages (each truncated to 500 characters) captured since the widget loaded on the page. This is stored on the ticket under `custom_fields.widget_context` and is not shown to the end user.

### Adding custom application context

Host apps can attach their own key/value context (e.g. current plan, feature flags, account ID) that gets included in the same snapshot:

```html
<script>
  window.HelpdeskWidget.setContext({ plan: "pro", accountId: "acct_123" });
</script>
```

Call it any time after the widget script has loaded — each call shallow-merges into the existing context. The combined payload is capped at ~4KB; calls that would exceed the cap are ignored with a console warning.

---

## CORS

The HayOps API enforces CORS using the `allowed_domains` list on each `SourceApplication`. Wildcard entries are supported — `"*.example.com"` matches any subdomain (but not the bare apex domain; list that separately if needed, e.g. `["example.com", "*.example.com"]`).

If a browser request is rejected with a CORS error:

1. Open the HayOps admin for the relevant environment
2. Find the `SourceApplication` for the client app
3. Add the missing domain to `allowed_domains` (as a JSON array of hostname strings, without protocol — e.g. `["app.example.com", "*.app.example.com", "localhost"]`)

---

## Verifying the integration

After setting the env vars and restarting the app:

1. Log in to the client application as a regular user.
2. A chat-bubble button should appear in the bottom-right corner.
3. Click it — the panel should open and show "No tickets yet."
4. Create a test ticket. It should appear immediately in the HayOps agent queue under the correct `SourceApplication`.
5. In the HayOps, the ticket's submitter email should match the logged-in user's email.

If the button does not appear, check:
- `HELPDESK_URL`, `HELPDESK_API_KEY`, and `HELPDESK_API_SECRET` are set and non-empty
- The widget version exists on the HayOps instance (the script URL returns 200, not 404)
- The browser console shows no CORS errors (if it does, check `allowed_domains`)
