# HayOps — Product Manager's Manual

Operating manual for whoever owns the HayOps: the product, the support queue, and the
people answering it. Written against the system as it actually exists (see
[README.md](../README.md) for the feature list and [CLAUDE.md](../CLAUDE.md) for deploy
mechanics), not against a generic support-desk playbook.

Two roles are referenced throughout and they may or may not be the same person:

- **HayOps PM** — owns the HayOps *as a product*. Backlog, releases, integrations with
  the product apps, the reporting story.
- **Support Ops Manager** — owns the *queue*. Triage, routing, coverage, SLA, the weekly
  report-out on how the team is doing.

Where a section applies to only one, it says so.

---

## Table of contents

1. [What you are managing](#1-what-you-are-managing)
2. [Roles, and who covers what](#2-roles-and-who-covers-what)
3. [The tier model](#3-the-tier-model)
4. [Running the support queue](#4-running-the-support-queue)
5. [Coverage: when the Ops Manager is away](#5-coverage-when-the-ops-manager-is-away)
6. [Measuring the team](#6-measuring-the-team)
7. [The report-out](#7-the-report-out)
8. [Product development: feedback and enhancements](#8-product-development-feedback-and-enhancements)
9. [Bug intake, triage, and won't-fix](#9-bug-intake-triage-and-wont-fix)
10. [Release process the PM owns](#10-release-process-the-pm-owns)
11. [Test plan](#11-test-plan)
12. [Known gaps and limitations](#12-known-gaps-and-limitations)
13. [Appendix A — templates](#appendix-a--templates)
14. [Appendix B — runbooks](#appendix-b--runbooks)

---

## 1. What you are managing

One Django install serving **all** of your products, including any added later. There
is one agent dashboard and one ticket table; the multi-tenancy is by hostname.

| Concept | What it is | Where the PM touches it |
|---|---|---|
| `SourceApplication` | One row per product. Holds API credentials, allowed widget origins, branding (product name, colour, logo, email signature), `portal_domain`, `mail_domain`. | Django admin → Core → Source applications |
| `portal_domain` | The customer-facing host, e.g. `support.example.com`. Serves the submit form, KB, magic-link ticket views, CSAT pages. | Admin + DNS |
| `mail_domain` | SES-verified inbound domain, e.g. `mail.example.com`. Mail to **any** local part at this domain opens a ticket for that app. | Admin + `just ses-setup <domain>` |
| `ADMIN_DOMAIN` | `helpdesk.example.com` — the staff UI. Agent routes 404 on portal hosts. | env, server-side |

**Four ways a ticket can arrive**, and you need all four working per app:

1. **Email** — SES receipt rule → S3 → SNS → webhook → background task. The recipient's
   *domain* selects the application. Published aliases are `support@`, `help@`, `bugs@`,
   `hello@`, but routing matches the domain only, so any local part works.
2. **Portal form** — the public submit form on `portal_domain`. Honeypot plus a per-IP
   rate limit of 5 submissions per hour.
3. **Widget** — the embeddable JS panel in the product itself. HMAC user auth, or
   anonymous app-key mode. Captures page URL, user agent, viewport, timezone, a rolling
   buffer of console errors, an optional annotated screenshot, and whatever the host app
   passes via `window.HelpdeskWidget.setContext({...})`. See [INTEGRATE.md](INTEGRATE.md).
4. **Agent-created** — staff open a ticket on a customer's behalf from the dashboard.

**The one operational fact that bites everyone:** background work runs on a
Procrastinate worker backed by Postgres. **If the worker is not running, no email is ever
sent** — no acknowledgements, no agent replies, no team alerts, no CSAT surveys. Tickets
still get created and the dashboard still looks fine. Silence from the outbound side is
the first thing to check when customers say they never heard back.

### Ticket vocabulary

**Statuses:** `new` → `open` → `pending` → `resolved` → `closed`, plus `merged` for
duplicates folded into another ticket.

- `new` — nobody has publicly replied yet. Posting a public staff reply flips it to
  `open` automatically.
- `pending` — waiting on the customer. Use it; it is what keeps the "we owe them a reply"
  count honest.
- `resolved` — we believe it is done. This stamps `resolved_at` and triggers the CSAT
  survey.
- `closed` — administratively finished. **No CSAT survey is sent.** Closing a ticket that
  was actually solved silently costs you the satisfaction datapoint.

**Priorities:** `low`, `normal`, `high`, `urgent`. Priority drives SLA overrides, not
routing.

**Definition of "first response"** (this is enforced in code, not by convention): the
first **public** comment written by a **staff** user. An internal note is not a response.
A canned acknowledgement *is*. That definition is what `first_responded_at` records and
what every response-time number on the dashboard is built from — brief the team on it,
because it is gameable and you should know if it is being gamed.

---

## 2. Roles, and who covers what

| Role | Owns | Escalation target for |
|---|---|---|
| **Support agent** (any team member) | Tier 1 tickets end to end. Triage of anything they open first. | — |
| **Support Ops Manager** | The queue as a whole. Routing, coverage, SLA, weekly report. | Anything unrouted or ageing |
| **Application PM** (per product) | Tier 2 tickets about their app: intended behaviour, account/config decisions, commercial judgement calls. | Tier 1 agents |
| **Finance / Stripe owner** | Tier 2 tickets touching billing, subscriptions, refunds, invoices, tax. | Tier 1 agents |
| **Dev team** | Tier 3. Reproducible defects, data fixes, anything needing code or DB access. | Tier 2 |
| **HayOps PM** | The HayOps product itself: backlog, releases, app integrations, reporting. | Ops Manager, for tooling gaps |

Two rules that prevent most of the failure modes:

- **A ticket always has exactly one owner.** `assigned_to` is that owner. Escalation
  *moves* ownership; it does not add a second owner. "I asked the dev team in Slack" with
  the ticket still assigned to the agent is the single most common way a ticket rots.
- **The escalating person stays the customer's point of contact** unless they explicitly
  hand that over in a public reply. Tier 3 talks to Tier 1, not usually to the customer.

---

## 3. The tier model

### Definitions

| Tier | Means | Who can take it | Typical content |
|---|---|---|---|
| **Tier 1** | Basic working knowledge of the app is enough. | Anyone on the support team. | How do I…, password/access, where is the button, known issue with a KB article, account questions with a documented answer. |
| **Tier 2** | Needs product authority or specialist knowledge. Two flavours: **2-PM** (the application's PM) and **2-FIN** (Finance/Stripe). | Named individuals only. | Is this intended behaviour, exception to policy, refund or proration, subscription state, invoice/tax questions, anything with money attached. |
| **Tier 3** | Needs the Dev team — code, logs, database, or infrastructure. | Dev team. | Reproducible defects, errors nobody can explain, data corruption, integration failures, anything requiring a deploy to fix. |

### How tiers are represented in the system

There is **no tier field**. Do not ask for one — `Queue` already does the job and gets you
filtering, per-ticket audit trail, and saved views for free.

Create these queues once (Django admin → Tickets → Queues):

| Queue name | Slug | Meaning |
|---|---|---|
| Tier 1 | `tier-1` | Default landing queue for everything |
| Tier 2 — Product | `tier-2-product` | Escalated to an application PM |
| Tier 2 — Finance | `tier-2-finance` | Escalated to Finance/Stripe |
| Tier 3 — Dev | `tier-3-dev` | Escalated to the Dev team |

Escalation is then **change the queue and reassign** — both are recorded as
`QUEUE_CHANGED` and `ASSIGNED` events on the ticket timeline, so escalation history is
auditable per ticket, and the queue filter (`?queue=tier-3-dev`) gives you a live view of
each tier's load.

Use **Tags** for the orthogonal axis — what the ticket is *about*, not who handles it:
`bug`, `feature-request`, `billing`, `stripe`, `access`, `data`, `wont-fix`, `shipped`.
A ticket can carry several. Tags are how you get from the queue to the product backlog
(§8, §9).

### Escalation procedure

Whoever escalates does all five steps. An escalation missing any of them goes back.

1. **Set the queue** to the receiving tier.
2. **Reassign** to the named person (Tier 2) or to the Dev-team triage owner (Tier 3).
   Never escalate to nobody.
3. **Write an internal note** containing: what the customer wants, what you already tried,
   what you need from the receiver, and the customer's deadline if there is one.
4. **Set the customer's expectation** with a public reply — what happens next and roughly
   when. Then set status `pending` if the ball is genuinely with the customer, or leave it
   `open` if the ball is with us. **Escalated is not `pending`.**
5. **Tag it** — at minimum `bug` or `feature-request` if it is either, since that is what
   feeds the product backlog.

**De-escalation** is the same in reverse: the resolving tier writes an internal note with
the answer in plain language, moves the queue back to `tier-1`, and reassigns to the
original agent, who talks to the customer. Tier 3 should very rarely be the one to send
the final reply.

### SLA and tiers

**Escalation does not restart the clock.** SLA due dates are stamped **once, at ticket
creation**, from the SLA policy matched at that moment. Changing the queue afterwards does
not re-stamp them. This is the correct customer-facing behaviour — our internal routing is
not the customer's problem — but it has a practical consequence:

> Key SLA policies on `source_application`, not on queue. The application is known at
> creation; the tier usually is not.

Configure in admin → Tickets → SLA policies. `priority_overrides` is JSON, e.g.:

```json
{"urgent": {"first_response_minutes": 15, "resolution_minutes": 120},
 "high":   {"first_response_minutes": 30, "resolution_minutes": 480}}
```

Policy matching is most-specific-first: queue + application → queue → application →
global. **Create a global fallback policy** (no queue, no application). Without a matching
policy, `apply_sla` returns early, no due dates are stamped, and the ticket is invisible
to SLA compliance reporting entirely.

Suggested starting targets — tune from your own data after a month:

| Priority | First response | Resolution |
|---|---|---|
| Urgent | 15 min | 4 h |
| High | 1 h | 1 business day |
| Normal | 4 h | 3 business days |
| Low | 1 business day | 10 business days |

The resolution target is a *whole-ticket* target regardless of tier. If Tier 3 work
routinely blows it, that is a staffing or prioritisation finding for the report-out, not a
reason to weaken the number.

---

## 4. Running the support queue

### The daily loop (Ops Manager, ~20 min, twice a day)

Work the dashboard at `https://helpdesk.example.com/tickets/`. The queue excludes
`closed` and `merged` by default and polls every 10 seconds.

1. **Unassigned, oldest first** — filter `assigned_to=unassigned`, sort `created_at`
   ascending. Everything here gets an owner before you move on. This is the whole job in
   one step.
2. **Triage each new ticket** — set the application if it landed as "Direct" (email from
   an unrecognised domain, or a widget in app-key-only mode), set priority, set the tier
   queue, tag it, assign it.
3. **Ageing check** — sort `-updated_at` descending and scroll to the bottom. Anything
   untouched for more than a day with status `open` needs a nudge or a status correction.
   `pending` tickets older than a week: chase once, then resolve with a "reopen any time"
   reply.
4. **Tier 2/3 check** — filter each escalation queue. Anything sitting more than 24 h with
   no internal note since escalation gets chased directly with the assignee.
5. **Merge duplicates** — same person, same issue, two tickets. Merge moves comments to
   the target and leaves a tombstone.

### Bulk actions

Select on the queue page and apply status, priority, or assignee to up to 200 tickets.
Notes:

- The **notify customers** checkbox is off by default. Bulk-resolving a backlog with it on
  sends a mail per ticket. Deliberate choice each time.
- Bulk cannot set status back to `new`, and cannot merge.
- **Bulk delete requires superuser** and destroys the audit trail with the ticket. It is
  for spam, not for tidying. Prefer `closed`.

### Tools the team should actually be using

| Tool | Where | Why the Ops Manager cares |
|---|---|---|
| **Canned responses** | Ticket reply box, and menu → Canned responses | Usage counts tell you which answers are load-bearing. A canned response used 50×/month is a KB article, and probably a product fix. |
| **Saved views** | Queue page → save current filters; can be shared | Shared views are how you hand the queue over (§5). |
| **Knowledge base** | `/kb/`, per-app or global articles | Agents can drop an article straight into a reply. Deflection lives here. |
| **Internal notes** | Ticket timeline | Do not count as first response. Say so in onboarding. |
| **Merge** | Ticket detail | Keeps duplicate volume from distorting every metric you report. |
| **Resend access link** | Ticket detail | For "I can't open the link you sent" — the most common customer-side failure. |

### Hygiene standards to hold the team to

- Every ticket has an owner within the first-response SLA window.
- Status reflects reality: `pending` only when we are genuinely waiting on the customer.
- Resolve, do not close, anything that was actually solved — closing skips the CSAT survey.
- Escalations carry an internal note (§3).
- Anything answered twice in a month becomes a KB article or a canned response.

---

## 5. Coverage: when the Ops Manager is away

The queue does not survive on a single person's attention. Standing arrangement:

**Named deputy, always.** Maintain a rota — a pinned doc or calendar is fine, this needs
no tooling. Every working day has exactly one **Queue Owner of the Day**. The Ops Manager
holds it by default; the deputy holds it on any day the Ops Manager is out.

**Before any planned absence, the Ops Manager:**

1. Names the deputy in writing to the whole support team and to the app PMs.
2. Ensures `SUPPORT_NOTIFY_EMAIL` reaches the deputy (it is a plain-text alert per new
   ticket and customer reply — the safety net if nobody opens the dashboard).
3. Hands over open escalations: for each ticket in a Tier 2/3 queue, a one-line status in
   the handover note (Appendix A).
4. Confirms the deputy has a staff account that works, and superuser if bulk delete might
   be needed.

**The deputy's minimum daily obligation** is step 1 of the daily loop — nothing unassigned
at end of day — plus escalating anything urgent. Full triage is a nice-to-have; an
unowned queue is not acceptable.

**Unplanned absence.** If the Queue Owner has not acted on the queue by mid-morning and is
unreachable, the deputy takes over without waiting for permission. Write this down; it is
the rule people hesitate over exactly when hesitating is expensive.

**Setup that makes handover a no-op:** create shared saved views —
`Unassigned`, `Urgent + High open`, `Tier 2 — Product`, `Tier 2 — Finance`,
`Tier 3 — Dev`, `Pending > 7 days`. The deputy works from the same views, not from a
description of what the Ops Manager usually does.

---

## 6. Measuring the team

The reporting dashboard is at `/reports/` (staff only). It defaults to the last 7 days
inclusive of today; `?date_from=&date_to=` accept `YYYY-MM-DD`.

### What the dashboard gives you today

| Metric | Definition in code | Read it as |
|---|---|---|
| Volume opened/closed | Per day, every day in range including zeroes | Load and whether you are keeping up |
| Avg first response time | Mean of `first_responded_at − created_at`, only over tickets that *have* a first response | Responsiveness — but see caveat below |
| SLA compliance % | Of tickets created in range with any due date: met both first-response and resolution due dates | Headline discipline number |
| Unclosed total / unclaimed | **Current state, not date-filtered.** `new`+`open`+`pending`+`resolved` | Backlog right now |
| Agent workload | Unclosed tickets per agent, busiest first | Distribution, not throughput |
| CSAT | Average 1–5 and count, daily plus overall | Quality, thin sample |
| Tickets by source application | Count per app, "Direct" where unset | Which product is generating load |

`unclaimed + Σ(per-agent) == unclosed` by construction — if those three ever disagree in a
screenshot, the screenshot is stale, not the maths.

### Caveats you must know before you quote a number

- **SLA compliance is pessimistic on short ranges.** A ticket created ten minutes ago with
  a 60-minute first-response target and no reply yet counts as *not met*. Today's number
  is always understated. Report SLA on a range ending at least a full resolution-target
  before now — for weekly reporting, run the report for the week ending *yesterday*.
- **Average first response excludes never-answered tickets entirely.** A ticket nobody
  ever replied to does not drag the average down; it simply is not in it. Always pair the
  average with the count of unanswered tickets, or the metric flatters the team exactly
  when it should not.
- **Agent workload is a snapshot of open load, not productivity.** It says nothing about
  how many tickets someone resolved. Do not use it for performance conversations.
- **There is no per-tier or per-queue reporting.** Tier mix has to be counted from the
  queue filters by hand until it is built (§12).
- **Nothing proactively flags an SLA breach.** The breach-detection code exists but is
  never scheduled (§12). Breaches surface only in the retrospective compliance number.
- **Duplicates inflate volume** unless they are merged. Merge discipline is a reporting
  concern, not just tidiness.
- **CSAT only reaches `resolved` tickets.** Teams that close rather than resolve will show
  a shrinking sample and you will misread it as apathy.

### The numbers worth holding the team to

Start here, adjust once you have a baseline month:

| Measure | Target | Source |
|---|---|---|
| First response within SLA | ≥ 90 % | SLA compliance, dashboard |
| Nothing unassigned overnight | 100 % of days | Unclaimed count at end of day |
| Tickets ageing > 7 days with status `open` | < 5 % of open | Queue, sort by `updated_at` |
| CSAT | ≥ 4.2 / 5 | Dashboard |
| Tier 1 resolution (no escalation) | ≥ 70 % of tickets | Count of tickets never leaving `tier-1` |
| Escalations acknowledged within 24 h | 100 % | Internal note after the queue change |

---

## 7. The report-out

Two audiences, two cadences. Both are short. The full template is in Appendix A.

### Weekly — support operations (Ops Manager → leadership + app PMs)

Range: the week ending yesterday, so the SLA number is honest. Five things:

1. **Load** — opened vs closed, and the direction of the backlog. One sentence on whether
   we are keeping up.
2. **Responsiveness** — SLA compliance %, average first response, and the count of
   tickets that got no response at all. The third number is the one that matters.
3. **Tier mix** — how many stayed Tier 1, how many escalated where. Escalation rate
   trending up means either a product regression or a knowledge gap; say which you think
   it is.
4. **Ageing and risk** — anything open more than a week, anything escalated and stalled,
   named with a ticket reference and an owner.
5. **Coverage** — who held the queue each day; any day it was unowned.

Plus **CSAT** with its sample size, and one line naming the top recurring theme with what
you are doing about it (KB article, canned response, product ticket).

### Monthly — helpdesk product (HayOps PM → leadership)

1. **State of the application** — releases shipped, versions in test and prod, incidents,
   deploy health.
2. **Integration status per app** — for each `SourceApplication`: widget version live,
   email in/out verified, portal reachable, branding correct.
3. **Backlog** — enhancements shipped, in flight, and next; bugs open by severity;
   won't-fix decisions taken this month and why.
4. **Support-driven insight** — what the ticket data says about the *products*, not the
   helpdesk. Ranked recurring themes with volume attached. This is the section the rest of
   the business actually wants; ranked-by-volume is the argument that gets things fixed.
5. **Risks and asks.**

---

## 8. Product development: feedback and enhancements

The HayOps PM runs a single funnel from "someone said something" to "it shipped".

### Intake

Feedback arrives from four places, and all four land in one backlog:

- **Agents** — the richest source. An agent hitting the same friction daily will not file
  a ticket about it unless asked. Ask, in a standing agenda item.
- **Customers** — via tickets. Tag `feature-request`.
- **Application PMs** — usually integration needs: a field they want captured, a webhook
  event, branding.
- **Metrics** — a canned response used 200 times is a feature request nobody wrote down.

### Weekly triage (HayOps PM + Ops Manager, 30 min)

Review everything tagged `feature-request` since last time, plus the agent list. For each:

| Decision | Meaning | Action |
|---|---|---|
| **Now** | Goes into the next release | Write it up, hand to Dev, link the ticket |
| **Next** | Agreed, not yet scheduled | Backlog with a rough size |
| **Someday** | Real but not worth doing yet | Backlog, no promise made |
| **No** | Not doing it | Reply to the requester with the reason, close |

Say "no" out loud and in writing. A backlog nobody ever declines from is a backlog nobody
believes.

Score with: how many tickets or agent-hours it removes, how many apps benefit (something
serving all four apps beats something serving one), whether it removes a manual step from
the Ops Manager's day, and cost.

### Handing work to Dev

An enhancement is ready for Dev when it has:

- **The problem**, in support terms, with evidence — ticket references or a count.
- **Who is hurt** — agents, customers, Ops Manager, an app PM.
- **Success criterion** — the observable thing that will be true afterward. "The Ops
  Manager stops counting tier mix by hand" beats "add tier reporting".
- **Scope boundary** — what is explicitly out.
- **Priority and target release.**

No implementation design. If a spec argues about database columns, the PM has drifted out
of their lane.

Template in Appendix A. Track work wherever the Dev team already tracks it; do not invent
a second tracker. **Link both directions**: the customer ticket gets an internal note with
the work item reference, so when it ships you can find the people to tell.

### When it ships

1. Verify on the test environment (§11) before it reaches prod.
2. Tag the originating tickets `shipped` and reply to the requesters — this is the cheapest
   goodwill in the whole operation and it is almost always skipped.
3. Tell the support team what changed *before* customers see it.
4. Update the KB and canned responses if behaviour changed.
5. Record it in the monthly report.

---

## 9. Bug intake, triage, and won't-fix

### Recognising one

A bug is: the product did something other than what it says it does. Not "the customer
was confused" (that is a docs or UX finding — still worth filing, tagged
`feature-request`), and not "the customer wanted something else" (that is a request).

### Intake standard

Every bug ticket needs, before it goes to Tier 3:

- **Reference number** of the customer ticket.
- **Which application**, and which environment.
- **Steps to reproduce**, numbered.
- **Expected vs actual.**
- **Frequency and scope** — one customer or many, always or intermittently.
- **Widget-captured context** where present: page URL, user agent, viewport, console
  errors, screenshot. This arrives automatically for widget-submitted tickets and shows in
  the ticket sidebar — use it, and copy the relevant parts into the escalation note.

An agent who cannot produce steps to reproduce should say so explicitly rather than
escalate a vague report. "Cannot reproduce; customer sees it every time on Safari" is
useful. Silence is not.

### Severity

| Severity | Definition | Response |
|---|---|---|
| **S1 — Critical** | Data loss, security exposure, or a core workflow unusable for many customers. No workaround. | Escalate to Tier 3 immediately regardless of hour. Ticket priority `urgent`. |
| **S2 — Major** | Significant feature broken; painful or no workaround; affects several customers. | Tier 3 same working day. Priority `high`. |
| **S3 — Minor** | Broken but with a workaround, or affects few customers. | Tier 3 at weekly triage. Priority `normal`. |
| **S4 — Cosmetic** | Visual or wording defect, no functional impact. | Batched. Priority `low`. |

Severity is about impact and is set by support. Priority (the order Dev works in) is set
jointly with Dev and may differ — an S3 that hits your largest customer can outrank an S2.
Do not let those two words become synonyms.

### The bug loop

1. **Report** — agent files with the intake standard above, tags `bug`, sets severity in
   the ticket, escalates to `tier-3-dev` with an internal note.
2. **Triage** (HayOps PM + Dev, weekly, plus ad hoc for S1/S2) — confirm severity,
   accept or decline, assign, target a release.
3. **Fix** — Dev works it on `dev`. The originating ticket stays `open` and owned by the
   agent, who keeps the customer informed. Do not resolve on "fix merged".
4. **Verify on test** — the fix is promoted to the test environment and someone from
   support confirms it against the original reproduction steps. **Support verifies, not
   the person who wrote the fix.**
5. **Ship** — released to prod via a version tag (§10).
6. **Close the loop** — reply to the customer, tag `shipped`, resolve the ticket so the
   CSAT survey goes out.

### Won't-fix

A legitimate and necessary outcome. A bug is won't-fix when the cost of fixing exceeds the
harm, the behaviour is actually intended, it is in a component being retired, it is not
reproducible after genuine effort, or it is caused by something outside our control.

**The won't-fix decision requires:**

- A named decider — HayOps PM for helpdesk bugs, the application PM for their product.
  Dev may recommend; Dev does not decide alone.
- **A written reason on the ticket**, in language a customer could read.
- **A reply to the customer** saying so, with the workaround if one exists. Never let a
  won't-fix reach the customer as silence.
- Tag `wont-fix`, then resolve.
- **Review at the next monthly** — won't-fix is a decision at a point in time. Three more
  customers hitting the same thing is new information; reopening is not an admission of
  error.

Cannot-reproduce is **not** won't-fix. Ask for more detail, keep it open a defined period
(two weeks is reasonable), then close with a note inviting the customer to reopen. Say
which one you are doing.

---

## 10. Release process the PM owns

Full mechanics are in [CLAUDE.md](../CLAUDE.md); this is the PM's slice.

Promotion is strictly one direction: **`dev` → `test` → `main`**. Never commit directly to
`test` or `main`, hotfixes included.

```bash
git switch dev && git push origin dev   # CI must be green
git push origin dev:test                # deploys test
# verify on test — this is the PM's gate (§11)
git push origin dev:main
git tag v1.4.0 && git push origin v1.4.0   # THIS is what deploys prod
```

Merging to `main` does not ship. The **tag** ships. Roll back by pushing an earlier tag.
Version numbers are semver: features bump the minor, fixes the patch.

**The PM's gate is step 3.** Nothing goes to prod without someone confirming on test that
the thing the release claims to do actually happens. That is what §11 is for.

**Widget releases are separate and worth watching.** The widget is versioned in its URL
(`/widget/v1.2.3/helpdesk-widget.js`) and each client app pins a version in its own
config. Shipping a new widget does **not** update any app — every product team must bump
`HELPDESK_WIDGET_VERSION` themselves. Track which version each app is running; that column
belongs in the monthly report.

---

## 11. Test plan

Three separate things, and conflating them is how releases go out untested:

- **A. Automated suite** — runs in CI on every push. Gates every deploy.
- **B. Release regression** — manual, on the test environment, before every production tag.
- **C. Application integration acceptance** — manual, per `SourceApplication`, when
  onboarding an app or changing its configuration.

### A. Automated suite

CI enforces `ruff check`, formatting, `makemigrations --check`, and the test suite. Run
locally with `just ci`. Both deploys gate on it. A red build does not get promoted, and
"it's only the linter" is not an exception — the same job gates both.

Current automated coverage, so you know what is *not* covered:

| Area | File | Covers |
|---|---|---|
| Bulk actions | `apps/tickets/tests/test_bulk_actions.py` | Bulk update/delete, permissions, `resolved_at` integrity |
| Inbound routing | `apps/inbound_email/tests/test_routing.py` | Domain→app routing, threading, dedupe, participant rules |
| Attachments | `apps/tickets/tests/test_attachments.py` | Upload, inline-render allowlist, storage |
| Customer login | `apps/portal/tests/test_customer_login.py` | Magic links, expiry, single use |
| Widget API | `apps/widget_api/tests/test_api.py` | Auth, ticket CRUD, app scoping |
| Notifications | `apps/tickets/tests/test_notifications.py` | Which mails fire on which transition |
| Reporting | `apps/reporting/tests.py` | Dashboard query correctness |
| Dashboard, canned, merge, staff creation, KB, portal, CORS, bounces, parser, storage, magic login, template comments | see `apps/*/tests/` | — |

**Not covered automatically** — these are why part B exists: real SES send and receive,
DNS and SES verification for a new domain, the widget running inside a real host app,
browser rendering, Cloudflare R2 storage against the live bucket, webhook delivery to a
real endpoint, branding cache expiry, and anything involving the worker actually running.

### B. Release regression — run on test before every production tag

Environment: `https://helpdesk-test.example.com`. **Confirm the worker is running
first** — without it every email step below fails and you will misdiagnose it as an email
configuration problem.

Record the pass in the **Testbench** (`/testbench/`) rather than on paper: start a run
named for the release, have testers claim cases as they go, and file failures as GitHub
issues from each case's history. Every application's manual regression script — and these
B/C checklists, once loaded into the helpdesk application's testbench with **Import JSON**
— then keeps its results per run. How to run it, and how to have Claude update a suite
through the MCP server, is on the in-app Help page (`/help/#testbench`).

**B1 — Ticket lifecycle (staff)**

| # | Step | Expected |
|---|---|---|
| 1 | Open the queue as staff | Loads; closed and merged excluded; live poll does not flicker |
| 2 | Filter by status, priority, queue, application, assignee; free-text search | Each filter narrows correctly; search matches subject and reference |
| 3 | Save a view; reload; open it | Filters restored |
| 4 | Create a ticket as staff on a customer's behalf | Visible to the customer via their portal link |
| 5 | Claim a ticket | Assigned to you; `ASSIGNED` event in timeline |
| 6 | Post an internal note | Not emailed; `first_responded_at` **not** set |
| 7 | Post a public reply | Status `new`→`open`; `first_responded_at` set; customer receives branded mail |
| 8 | Change priority, queue, tags, application | Each recorded as its own timeline event |
| 9 | Merge a duplicate into it | Comments move; source shows as merged |
| 10 | Resolve | `resolved_at` set; CSAT survey mail sent |
| 11 | Submit the CSAT rating | Appears on the reporting dashboard |
| 12 | Close a different ticket | No CSAT mail (confirming the resolve/close distinction) |

**B2 — Tiering and escalation**

| # | Step | Expected |
|---|---|---|
| 1 | Move a ticket `tier-1` → `tier-2-product`, reassign | Both events on the timeline |
| 2 | Filter the queue by `tier-2-product` | Ticket present |
| 3 | De-escalate back to `tier-1` and reassign | Both events; original agent owns it |
| 4 | Check SLA due dates before and after escalation | **Unchanged** — clock does not reset |

**B3 — Bulk actions**

| # | Step | Expected |
|---|---|---|
| 1 | Select several, set status + assignee, notify **off** | Updated; no customer mail |
| 2 | Repeat with notify **on** | One mail per ticket |
| 3 | Bulk-resolve, then inspect `resolved_at` | Stamped correctly, not clobbered on already-resolved tickets |
| 4 | Attempt bulk delete as non-superuser | Forbidden |

**B4 — Email, inbound**

| # | Step | Expected |
|---|---|---|
| 1 | Mail `support@<test mail_domain>` from an external address | Ticket created against the right application |
| 2 | Mail a nonsense local part at the same domain | Same application (routing is domain-only) |
| 3 | Reply to the acknowledgement | Appends as a comment, no duplicate ticket |
| 4 | Reply with the `[HD-xxxx]` subject tag stripped | Still threads (Message-ID/References fallback) |
| 5 | Send the same message twice | Deduped |
| 6 | Reply from an address that is not a participant | Does **not** append to the existing ticket |
| 7 | Send a bounce | No ticket opened; address flagged |
| 8 | Send with an attachment | Attached; images open in a tab, other types download |

**B5 — Email, outbound**

Verify each mail is branded for its application (product name, colour, logo, signature),
comes from `ticket+<ref>@<mail_domain>`, and threads:

acknowledgement on creation · agent reply · status change · CSAT survey on resolve ·
plain-text team alert to `SUPPORT_NOTIFY_EMAIL` on new ticket and on customer reply.

**B6 — Customer portal** (per `portal_domain`)

| # | Step | Expected |
|---|---|---|
| 1 | Open the portal host | Correct branding |
| 2 | Submit the public form | Ticket created; acknowledgement with magic link received |
| 3 | Follow the magic link | Ticket visible and repliable |
| 4 | Follow the same link again | Rejected — single use |
| 5 | Wait past TTL, follow a fresh link | Rejected — expired |
| 6 | Submit 6 times from one IP within an hour | 6th rate-limited (429) |
| 7 | Fill the honeypot field | Silently rejected |
| 8 | Browse the KB on this portal | This app's articles plus global ones; no other app's |
| 9 | Request a staff URL on the portal host | 404 |
| 10 | Request a portal URL for a ticket belonging to another app | Not accessible |

**B7 — Widget** (in a real host app, per app)

| # | Step | Expected |
|---|---|---|
| 1 | Load the host app | Widget appears, themed from the application's branding |
| 2 | Signed-in user with a valid HMAC hash | Their existing tickets listed |
| 3 | Submit a ticket with an annotated screenshot | Ticket created; screenshot attached |
| 4 | Inspect the ticket sidebar as staff | Page URL, user agent, viewport, timezone, console errors, and any `setContext` data present |
| 5 | Reply from within the widget | Appears on the ticket |
| 6 | Load the widget from a domain **not** in `allowed_domains` | CORS blocked |
| 7 | Tamper with the user hash | Rejected |
| 8 | App-key-only mode | Anonymous submission works; **no other app's tickets are visible** |

**B8 — Reporting**

| # | Step | Expected |
|---|---|---|
| 1 | Open `/reports/` | Charts render; do not resize themselves |
| 2 | Set an explicit date range | Every day in range plotted, including zeroes |
| 3 | Hand-edit the date param to garbage | Falls back to the default range, no 500 |
| 4 | Cross-check | `unclaimed + Σ(per agent) == unclosed` |
| 5 | Open as a non-staff user | Forbidden |

**B9 — Permissions and multi-tenancy**

Non-staff hitting staff routes → redirected or forbidden. Customers see only their own
tickets. Bulk delete → superuser only. Portal hosts never serve agent pages. One
application's widget key never returns another application's tickets.

**B10 — Migrations and deploy** (see CLAUDE.md)

`makemigrations --check --dry-run` clean before pushing. Migrations backward-compatible
with the code already running — destructive changes are a two-release expand/contract.
Never edit an applied migration. Then verify from inside the container:

```bash
docker exec helpdesk-<env>-app curl -sf -o /dev/null -w '%{http_code}' \
  -H 'Host: localhost' -H 'X-Forwarded-Proto: https' http://localhost:8000/ht/
```

Both headers required. Check the public URL too, to prove real traffic works.

### C. Application integration acceptance

Run for every new `SourceApplication`, and again whenever its domains, branding, or widget
version change. Test environment first, then production.

**Prerequisites**

- [ ] `SourceApplication` created; `api_key` / `api_secret` issued
- [ ] `allowed_domains` lists every origin that will load the widget
- [ ] `portal_domain` set and DNS pointed
- [ ] `mail_domain` set; `just ses-setup <domain>` run; SES verification complete
- [ ] Branding filled in: product name, colour, logo, email signature
- [ ] SLA policy exists for this application (or the global fallback is in place)
- [ ] Host app configured with `HELPDESK_URL`, `HELPDESK_API_KEY`, `HELPDESK_API_SECRET`,
      `HELPDESK_WIDGET_VERSION`

**Acceptance**

| # | Check | Pass condition |
|---|---|---|
| 1 | Portal loads on `portal_domain` | Correct branding, no cross-app leakage |
| 2 | Portal submission | Ticket tagged to this application, not "Direct" |
| 3 | Inbound mail to each alias | Ticket against this application |
| 4 | Outbound mail | From this app's `mail_domain`, this app's branding and signature |
| 5 | Widget loads in the host app | Correct theme, correct version |
| 6 | Widget auth | Signed-in identity resolves; tampered hash rejected |
| 7 | CORS | Allowed origins work; others blocked |
| 8 | KB scoping | This app's articles plus global; no others |
| 9 | Webhooks, if configured | Delivered, HMAC-signed, retried on failure |
| 10 | Custom fields, if defined | Present on the ticket form and stored |
| 11 | Reporting | App appears in the per-application breakdown |
| 12 | Branding change | Visible within 5 minutes, or immediately after clearing `source_app_domain:<host>` |

**Sign-off:** HayOps PM confirms 1–12; the application PM confirms 5–8 in their own app.
Record the date and the widget version tested.

### Regression triggers

Run the full B suite on any release touching ticket lifecycle, email, permissions, or
multi-tenancy. A widget-only release needs B7 and C5–C8. A reporting-only release needs
B8. Everything gets B10.

---

## 12. Known gaps and limitations

Verified against the code as of this writing. These are the PM's standing backlog and the
things not to promise in a report.

**1. SLA breach detection never runs.** `SLAService.check_sla_breaches()` exists and works
but has no caller — no periodic task, no management command. Nothing alerts on a breach as
it happens; breaches only appear retrospectively in the compliance percentage. *Impact:*
the team finds out it missed an SLA a week later. *Fix:* schedule it as a periodic
Procrastinate task alongside the existing 5-minute SES sweep. **This is the highest-value
small item in the backlog.**

**2. SLA compliance is understated on recent ranges.** In-flight tickets inside their
window count as missed. Workaround: report on ranges ending before now (§6).

**3. Average first response ignores never-answered tickets.** Always publish it alongside
the unanswered count.

**4. No per-tier or per-queue reporting.** Tier mix, the core of the weekly escalation
story, is counted by hand from queue filters. *Fix:* add a per-queue breakdown to the
reporting dashboard.

**5. No agent throughput metrics.** Only current workload. There is no resolved-per-agent,
no per-agent response time, no reopen rate.

**6. No ticket-age report.** Ageing is found by sorting the queue, not by a number.

**7. SLA is stamped once at creation.** A ticket created without a queue never picks up a
queue-specific policy. Key policies on application (§3).

**8. Branding is cached for 5 minutes.** A changed name, colour, logo, or signature keeps
serving the old value on the portal and in outgoing mail until the entry expires. To pick
it up immediately: `cache.delete("source_app_domain:support.<domain>")`.

**9. Everything asynchronous needs the worker.** No worker, no email of any kind, silently.

**10. Closing skips CSAT.** Only `resolved` triggers the survey.

**11. Bulk delete destroys the audit trail.** Superuser-gated deliberately; prefer `closed`.

**12. Tier is a convention, not a constraint.** Nothing stops a ticket sitting in
`tier-3-dev` with a Tier 1 assignee. Process, not code, enforces the model.

---

## Appendix A — templates

### Weekly support report

```
HELPDESK — SUPPORT REPORT
Week ending: <date>  (report range ends yesterday so SLA is accurate)
Queue owner(s) this week: <names, per day>

LOAD
  Opened: N    Closed: N    Backlog: N (was N)
  Per application: <app>: N | <app>: N | ...

RESPONSIVENESS
  SLA compliance: N%   (target 90%)
  Avg first response: Nh Nm
  Tickets with NO response yet: N        <- the number that matters
  Ageing >7 days and still open: N

TIER MIX
  Resolved at Tier 1: N (N%)
  Escalated: Tier 2-Product N | Tier 2-Finance N | Tier 3-Dev N
  Escalations stalled >24h: <refs, owners>

SATISFACTION
  CSAT: N.N / 5 (n = N)

AT RISK — needs a decision
  HD-xxxx  <one line>  owner: <name>
  HD-xxxx  <one line>  owner: <name>

THEME OF THE WEEK
  <recurring issue>: N tickets. Action: <KB article / canned response / product ticket>

COVERAGE
  <any day the queue had no owner, and why>
```

### Monthly helpdesk product report

```
HELPDESK — PRODUCT REPORT
Month: <month>

STATE OF THE APPLICATION
  Released: <versions and dates>   Prod: <tag>   Test: <tag>
  Incidents: <count, one line each>

INTEGRATIONS
  App            Widget   Email in/out   Portal   Branding
  Acme           1.2.3    ok / ok        ok       ok
  Globex         ...

BACKLOG
  Shipped: <items, with originating ticket refs>
  In flight: <items, target release>
  Next: <items>
  Bugs open: S1 N | S2 N | S3 N | S4 N
  Won't-fix decided this month: <item — reason — decider>

WHAT SUPPORT DATA SAYS ABOUT THE PRODUCTS
  1. <theme> — N tickets across <apps> — recommendation
  2. ...

RISKS AND ASKS
```

### Escalation internal note

```
ESCALATING TO: Tier <N> — <queue>   ASSIGNEE: <name>
CUSTOMER WANTS: <one line>
ALREADY TRIED: <what and what happened>
NEED FROM YOU: <the specific question or action>
CUSTOMER DEADLINE: <date, or none>
CUSTOMER HAS BEEN TOLD: <what you promised them>
```

### Bug report (support → dev)

```
TICKET: HD-xxxx          APPLICATION: <app>       ENVIRONMENT: prod / test
SEVERITY: S1 / S2 / S3 / S4
REPRO:
  1. ...
  2. ...
EXPECTED:
ACTUAL:
FREQUENCY: always / intermittent — <n> customers affected
WIDGET CONTEXT: <URL, browser, viewport, console errors — if widget-submitted>
WORKAROUND: <or none>
```

### Enhancement request (PM → dev)

```
TITLE:
PROBLEM: <in support terms>
EVIDENCE: <ticket refs / counts / canned-response usage>
WHO IS HURT: agents / customers / ops manager / app PM
SUCCESS CRITERION: <the observable thing that will be true afterward>
OUT OF SCOPE:
PRIORITY: now / next / someday      TARGET RELEASE:
ORIGINATING TICKETS: HD-xxxx, HD-xxxx   <- so we can tell them when it ships
```

### Queue handover

```
QUEUE HANDOVER
From: <name>   To: <name>   Covering: <dates>

OPEN ESCALATIONS
  HD-xxxx  Tier <N>  <owner>  <status, next step>

WATCH LIST — customers or tickets needing care
  HD-xxxx  <why>

IN FLIGHT WITH DEV
  <item>  <expected>

ANYTHING PROMISED TO A CUSTOMER THIS WEEK
  <what, to whom, by when>

Deputy has: staff login confirmed / SUPPORT_NOTIFY_EMAIL delivery confirmed /
superuser if needed / shared saved views visible
```

---

## Appendix B — runbooks

### Onboarding a new application

1. Django admin → Core → Source applications → Add. Name, slug, `allowed_domains`,
   `portal_domain`, `mail_domain`. Save; copy `api_key` and `api_secret` (the secret is
   server-side only and never goes in a browser).
2. Branding: product name, primary colour, logo, email signature.
3. `just ses-setup <domain>` for DNS and SES.
4. Point `portal_domain` DNS at the helpdesk.
5. Create an SLA policy for the application, or confirm the global fallback covers it.
6. Hand the host-app team [INTEGRATE.md](INTEGRATE.md) and the four environment variables.
7. Run the §11-C acceptance checklist on test, then production.
8. Brief the support team: what the app does, who its PM is, common issues, and add a KB
   category.

### "Customers say they never got our reply"

1. Is the worker running? Nothing outbound happens without it — check first, always.
2. Is the address flagged in Core → Bounced addresses? One permanent bounce flags that
   mailbox everywhere.
3. Are SES delivery events being recorded (`AWS_SES_CONFIGURATION_SET`)? Without it,
   undelivered mail leaves no trace.
4. Does the ticket's application have the right `mail_domain`, and is it SES-verified?
5. Use **Resend access link** on the ticket for magic-link failures specifically.

### "A ticket came in as Direct"

`source_application` is unset — the sender's domain matched no `mail_domain`, or the
widget ran in app-key-only mode without one. Set the application on the ticket (it is
recorded as an event), then work out why: an alias customers use that is not configured,
or a host app misconfiguration.

### Branding change not showing

Cached for 5 minutes per portal hostname. Wait, or clear
`source_app_domain:<portal hostname>` from the cache.

### Deploy verification

See §11-B10 and CLAUDE.md. Both `Host` and `X-Forwarded-Proto` headers are required or the
health check fails misleadingly.
