# HayOps Application - Task Runner

set dotenv-load

# Default: list available recipes
default:
    @just --list

# --- Setup ---

# Install all dependencies
install:
    uv sync --extra dev

# Start Docker services (PostgreSQL)
up:
    docker compose up -d

# Stop Docker services
down:
    docker compose down

# Full project setup: install deps, start services, migrate, build CSS
setup: install up
    sleep 2
    just migrate
    just tailwind

# --- Django ---

# Run the development server
# Uses portless (https://helpdesk.localhost) when installed, plain runserver otherwise.
server:
    #!/usr/bin/env bash
    if command -v portless >/dev/null; then
        exec portless helpdesk bash -c 'uv run python manage.py runserver $HOST:$PORT'
    fi
    exec uv run python manage.py runserver 127.0.0.1:8000

# Run migrations
migrate:
    uv run python manage.py migrate

# Generate new migrations
makemigrations *ARGS:
    uv run python manage.py makemigrations {{ ARGS }}

# Create a superuser
createsuperuser:
    uv run python manage.py createsuperuser

# Open Django shell
shell:
    uv run python manage.py shell

# Run Django system checks
check:
    uv run python manage.py check

# Collect static files
collectstatic:
    uv run python manage.py collectstatic --noinput

# --- Background Worker ---

# Run the Procrastinate task worker
worker:
    uv run python manage.py procrastinate worker

# --- Tailwind ---

# Build Tailwind CSS
tailwind:
    uv run python manage.py tailwind build

# Watch Tailwind CSS for changes (dev mode)
tailwind-watch:
    uv run python manage.py tailwind watch

# --- Widget ---

# Install widget dependencies
widget-install:
    cd widget && npm install

# Build the embeddable widget
widget-build:
    cd widget && npm run build

# Run widget dev server
widget-dev:
    cd widget && npm run dev

# --- Testing ---

# Run all tests
test *ARGS:
    uv run pytest {{ ARGS }}

# Run tests with verbose output
test-verbose *ARGS:
    uv run pytest -v {{ ARGS }}

# --- Linting ---

# Run ruff linter
lint:
    uv run ruff check .

# Fix linting issues automatically
lint-fix:
    uv run ruff check --fix .

# Format code with ruff
fmt:
    uv run ruff format .

# Check formatting without modifying files
fmt-check:
    uv run ruff format --check .

# --- Development ---

# Run server + tailwind watcher + worker concurrently
dev:
    #!/usr/bin/env bash
    if command -v portless >/dev/null; then
        portless proxy start --https -p 443 2>/dev/null || true
        echo "  - HayOps: https://helpdesk.localhost/"
    else
        echo "  - HayOps: http://localhost:8000/"
    fi
    echo ""
    echo "Press Ctrl+C to stop all services"
    trap 'kill 0' EXIT
    (just tailwind-watch 2>&1 | sed 's/^/[tailwind] /') &
    (just worker 2>&1 | sed 's/^/[worker]   /') &
    (just server 2>&1 | sed 's/^/[django]   /') &
    wait

# Reset the database (destructive!)
[confirm("This will destroy all data. Continue?")]
db-reset:
    docker compose down -v
    docker compose up -d
    sleep 2
    just migrate

# Seed example data: admin login, an example app with its own portal, tickets
seed:
    uv run python manage.py seed_example

# --- SES / Domain Setup ---

# Configure SES + Cloudflare DNS for a new app domain (e.g., just ses-setup example.com)
ses-setup domain:
    #!/usr/bin/env bash
    set -euo pipefail

    DOMAIN="{{ domain }}"
    WEB_HOST="support.${DOMAIN}"
    MAIL_HOST="mail.${DOMAIN}"
    REGION="${AWS_S3_REGION_NAME:-us-east-1}"
    CNAME_TARGET="${HELPDESK_CNAME_TARGET:?set HELPDESK_CNAME_TARGET to your helpdesk host}"

    if [ -z "${CLOUDFLARE_API_TOKEN:-}" ]; then
        echo "ERROR: CLOUDFLARE_API_TOKEN is required" >&2
        exit 1
    fi

    CF_HEADERS=(-H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" -H "Content-Type: application/json")

    echo "==> Setting up web ${WEB_HOST} + mail ${MAIL_HOST}"
    echo ""

    # --- Step 1: Look up Cloudflare zone ID ---
    echo "--- Step 1: Looking up Cloudflare zone for ${DOMAIN} ---"
    ZONE_RESPONSE=$(curl -s "https://api.cloudflare.com/client/v4/zones?name=${DOMAIN}" "${CF_HEADERS[@]}")
    ZONE_ID=$(echo "$ZONE_RESPONSE" | python3 -c "import sys,json; r=json.load(sys.stdin); print(r['result'][0]['id'] if r['result'] else '')")
    if [ -z "$ZONE_ID" ]; then
        echo "ERROR: Could not find Cloudflare zone for ${DOMAIN}" >&2
        exit 1
    fi
    echo "  Zone ID: ${ZONE_ID}"

    # Helper: create or update a DNS record (idempotent)
    cf_dns_upsert() {
        local type="$1" name="$2" content="$3" proxied="${4:-false}" priority="${5:-}"

        EXISTING=$(curl -s "https://api.cloudflare.com/client/v4/zones/${ZONE_ID}/dns_records?type=${type}&name=${name}" "${CF_HEADERS[@]}")
        RECORD_ID=$(echo "$EXISTING" | python3 -c "import sys,json; r=json.load(sys.stdin); print(r['result'][0]['id'] if r['result'] else '')" 2>/dev/null || echo "")

        local data="{\"type\":\"${type}\",\"name\":\"${name}\",\"content\":\"${content}\",\"proxied\":${proxied}}"
        if [ -n "$priority" ]; then
            data="{\"type\":\"${type}\",\"name\":\"${name}\",\"content\":\"${content}\",\"proxied\":${proxied},\"priority\":${priority}}"
        fi

        if [ -n "$RECORD_ID" ]; then
            echo "  Updating ${type} record for ${name}"
            curl -s -X PUT "https://api.cloudflare.com/client/v4/zones/${ZONE_ID}/dns_records/${RECORD_ID}" \
                "${CF_HEADERS[@]}" -d "$data" > /dev/null
        else
            echo "  Creating ${type} record for ${name}"
            curl -s -X POST "https://api.cloudflare.com/client/v4/zones/${ZONE_ID}/dns_records" \
                "${CF_HEADERS[@]}" -d "$data" > /dev/null
        fi
    }

    # --- Step 2: Create/verify DNS records ---
    echo ""
    echo "--- Step 2: DNS records in Cloudflare ---"

    # CNAME (proxied) for the customer portal
    cf_dns_upsert "CNAME" "${WEB_HOST}" "${CNAME_TARGET}" "true"

    # MX record for SES inbound
    cf_dns_upsert "MX" "${MAIL_HOST}" "inbound-smtp.${REGION}.amazonaws.com" "false" "10"

    # SPF
    cf_dns_upsert "TXT" "${MAIL_HOST}" "v=spf1 include:amazonses.com ~all" "false"

    # DMARC
    cf_dns_upsert "TXT" "_dmarc.${MAIL_HOST}" "v=DMARC1; p=quarantine; pct=100" "false"

    echo "  DNS records configured"

    # --- Step 3: Verify domain in SES ---
    echo ""
    echo "--- Step 3: SES domain identity ---"
    SES_EXISTING=$(aws sesv2 get-email-identity --email-identity "${MAIL_HOST}" --region "${REGION}" 2>/dev/null || echo "")
    if [ -z "$SES_EXISTING" ]; then
        echo "  Creating SES email identity for ${MAIL_HOST}"
        aws sesv2 create-email-identity --email-identity "${MAIL_HOST}" --region "${REGION}"
    else
        echo "  SES identity already exists for ${MAIL_HOST}"
    fi

    # --- Step 4: DKIM CNAME records ---
    echo ""
    echo "--- Step 4: DKIM records ---"
    DKIM_JSON=$(aws sesv2 get-email-identity --email-identity "${MAIL_HOST}" --region "${REGION}" 2>/dev/null || echo "")
    if [ -n "$DKIM_JSON" ]; then
        DKIM_TOKENS=$(echo "$DKIM_JSON" | python3 -c "import sys,json;d=json.load(sys.stdin);[print(t) for t in d.get('DkimAttributes',{}).get('Tokens',[])]" 2>/dev/null || echo "")

        for TOKEN in $DKIM_TOKENS; do
            cf_dns_upsert "CNAME" "${TOKEN}._domainkey.${MAIL_HOST}" "${TOKEN}.dkim.amazonses.com" "false"
        done
        echo "  DKIM records configured"
    else
        echo "  WARNING: Could not fetch DKIM tokens (domain may still be provisioning)"
    fi

    # --- Step 5: SES receipt rule ---
    echo ""
    echo "--- Step 5: SES receipt rule ---"
    RULE_SET_NAME="helpdesk-inbound"
    RULE_NAME="helpdesk-catch-all"

    # Ensure rule set exists
    aws ses describe-receipt-rule-set --rule-set-name "${RULE_SET_NAME}" --region "${REGION}" 2>/dev/null || \
        aws ses create-receipt-rule-set --rule-set-name "${RULE_SET_NAME}" --region "${REGION}" 2>/dev/null || true

    # Check if rule exists and get current recipients
    RULE_JSON=$(aws ses describe-receipt-rule --rule-set-name "${RULE_SET_NAME}" --rule-name "${RULE_NAME}" --region "${REGION}" 2>/dev/null || echo "")
    if [ -n "$RULE_JSON" ]; then
        # Check if domain already in recipients
        HAS_DOMAIN=$(echo "$RULE_JSON" | python3 -c "import sys,json;r=json.load(sys.stdin).get('Rule',{}).get('Recipients',[]);print('yes' if '${MAIL_HOST}' in r else 'no')" 2>/dev/null || echo "no")

        if [ "$HAS_DOMAIN" = "yes" ]; then
            echo "  ${MAIL_HOST} already in receipt rule"
        else
            # Add domain to existing rule recipients
            UPDATED_RECIPIENTS=$(echo "$RULE_JSON" | python3 -c "import sys,json;r=json.load(sys.stdin).get('Rule',{});l=r.get('Recipients',[]);l.append('${MAIL_HOST}');print(json.dumps(l))")
            # Update the rule with new recipients list
            EXISTING_RULE=$(echo "$RULE_JSON" | python3 -c "import sys,json;r=json.load(sys.stdin).get('Rule',{});r['Recipients']=json.loads('${UPDATED_RECIPIENTS}');print(json.dumps(r))")
            aws ses update-receipt-rule --rule-set-name "${RULE_SET_NAME}" --rule "${EXISTING_RULE}" --region "${REGION}"
            echo "  Added ${MAIL_HOST} to receipt rule"
        fi
    else
        # Create new receipt rule
        SES_BUCKET="${SES_INBOUND_BUCKET:?set SES_INBOUND_BUCKET}"
        SNS_TOPIC="${SES_INBOUND_TOPIC_ARN:-}"
        ACTIONS="[{\"S3Action\":{\"BucketName\":\"${SES_BUCKET}\",\"ObjectKeyPrefix\":\"incoming/\"}}"
        if [ -n "$SNS_TOPIC" ]; then
            ACTIONS="${ACTIONS},{\"SNSAction\":{\"TopicArn\":\"${SNS_TOPIC}\",\"Encoding\":\"UTF-8\"}}"
        fi
        ACTIONS="${ACTIONS}]"

        RULE="{\"Name\":\"${RULE_NAME}\",\"Enabled\":true,\"Recipients\":[\"${MAIL_HOST}\"],\"Actions\":${ACTIONS},\"ScanEnabled\":true}"
        aws ses create-receipt-rule --rule-set-name "${RULE_SET_NAME}" --rule "${RULE}" --region "${REGION}"
        echo "  Created receipt rule with ${MAIL_HOST}"
    fi

    # A rule set does nothing until it is the active one (idempotent)
    ACTIVE_SET=$(aws ses describe-active-receipt-rule-set --region "${REGION}" --query 'Metadata.Name' --output text 2>/dev/null || echo "")
    if [ "$ACTIVE_SET" != "${RULE_SET_NAME}" ]; then
        aws ses set-active-receipt-rule-set --rule-set-name "${RULE_SET_NAME}" --region "${REGION}"
        echo "  Activated receipt rule set ${RULE_SET_NAME}"
    fi

    # --- Summary ---
    echo ""
    echo "==> Setup complete for ${DOMAIN}"
    echo ""
    echo "  Web:   https://${WEB_HOST} -> ${CNAME_TARGET} (Cloudflare proxied)"
    echo "  Email: *@${MAIL_HOST} -> SES -> S3 -> webhook"
    echo "  DKIM:  Check status with: just ses-status ${DOMAIN}"
    echo ""
    echo "  Next: Set portal_domain='${WEB_HOST}' and mail_domain='${MAIL_HOST}' on the SourceApplication in Django admin,"
    echo "        and add ${WEB_HOST} to ALLOWED_HOSTS + CSRF_TRUSTED_ORIGINS in the prod env."

# Show SES + DNS status for a domain (e.g., just ses-status example.com)
ses-status domain:
    #!/usr/bin/env bash
    set -euo pipefail

    DOMAIN="{{ domain }}"
    SUBDOMAIN="mail.${DOMAIN}"
    REGION="${AWS_S3_REGION_NAME:-us-east-1}"

    if [ -z "${CLOUDFLARE_API_TOKEN:-}" ]; then
        echo "ERROR: CLOUDFLARE_API_TOKEN is required" >&2
        exit 1
    fi

    CF_HEADERS=(-H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" -H "Content-Type: application/json")

    echo "==> Status for ${SUBDOMAIN}"
    echo ""

    # SES identity status
    echo "--- SES Domain Identity ---"
    SES_JSON=$(aws sesv2 get-email-identity --email-identity "${SUBDOMAIN}" --region "${REGION}" 2>/dev/null || echo "")
    if [ -n "$SES_JSON" ]; then
        echo "$SES_JSON" | python3 -c "import sys,json;d=json.load(sys.stdin);dk=d.get('DkimAttributes',{});ts=dk.get('Tokens',[]);print('  Verified:',d.get('VerifiedForSendingStatus',False));print('  DKIM Status:',dk.get('Status','N/A'));print('  DKIM Signing:',dk.get('SigningEnabled',False));print('  DKIM Tokens:',', '.join(ts)) if ts else None"
    else
        echo "  Not configured in SES"
    fi

    # Cloudflare DNS records
    echo ""
    echo "--- Cloudflare DNS Records ---"
    ZONE_RESPONSE=$(curl -s "https://api.cloudflare.com/client/v4/zones?name=${DOMAIN}" "${CF_HEADERS[@]}")
    ZONE_ID=$(echo "$ZONE_RESPONSE" | python3 -c "import sys,json; r=json.load(sys.stdin); print(r['result'][0]['id'] if r['result'] else '')" 2>/dev/null || echo "")

    if [ -n "$ZONE_ID" ]; then
        DNS_RECORDS=$(curl -s "https://api.cloudflare.com/client/v4/zones/${ZONE_ID}/dns_records?name=${SUBDOMAIN}" "${CF_HEADERS[@]}")
        echo "$DNS_RECORDS" | python3 -c "import sys,json;[print('  %-6s %s -> %s%s%s'%(r['type'],r['name'],r['content'],' (proxied)' if r.get('proxied') else '',(' (priority: %s)'%r['priority']) if r.get('priority') else '')) for r in json.load(sys.stdin).get('result',[])]"

        # Also check DKIM and DMARC records
        DKIM_RECORDS=$(curl -s "https://api.cloudflare.com/client/v4/zones/${ZONE_ID}/dns_records?name=_domainkey.${SUBDOMAIN}&type=CNAME" "${CF_HEADERS[@]}")
        echo "$DKIM_RECORDS" | python3 -c "import sys,json;[print('  %-6s %s -> %s'%(r['type'],r['name'],r['content'])) for r in json.load(sys.stdin).get('result',[])]"

        DMARC_RECORDS=$(curl -s "https://api.cloudflare.com/client/v4/zones/${ZONE_ID}/dns_records?name=_dmarc.${SUBDOMAIN}&type=TXT" "${CF_HEADERS[@]}")
        echo "$DMARC_RECORDS" | python3 -c "import sys,json;[print('  %-6s %s -> %s'%(r['type'],r['name'],r['content'])) for r in json.load(sys.stdin).get('result',[])]"
    else
        echo "  Zone not found for ${DOMAIN}"
    fi

    # SES receipt rule membership
    echo ""
    echo "--- SES Receipt Rule ---"
    RULE_JSON=$(aws ses describe-receipt-rule --rule-set-name "helpdesk-inbound" --rule-name "helpdesk-catch-all" --region "${REGION}" 2>/dev/null || echo "")
    if [ -n "$RULE_JSON" ]; then
        echo "$RULE_JSON" | python3 -c "import sys,json;r=json.load(sys.stdin).get('Rule',{}).get('Recipients',[]);s='${SUBDOMAIN}';print('  %s is %sin the receipt rule'%(s,'' if s in r else 'NOT '));print('  All recipients:',', '.join(r))"
    else
        echo "  Receipt rule not found"
    fi

# --- CI ---

# Run all CI checks
ci: lint fmt-check check test
