# Writing a testbench QC suite

A suite is the manual regression script testers run against an application's test
environment before a release. It is written for testers, not developers: every case gives
the setup, numbered steps, and a plain statement of what should happen, so a tester can
judge pass or fail without reading code.

## Workflow

1. `list_applications` to find the application's slug.
2. `get_suite` to download what exists today. Compare it against the application's current
   behaviour: its routes and pages, recent changes and release notes, and anything the user
   says the release touches. Look for features with no cases, cases for features that were
   removed or renamed, and steps whose URLs or labels no longer match the UI.
3. Before rewriting a case that has failed before, read `get_case_history`: past notes say
   what testers actually found.
4. Draft the complete suite as JSON, keeping every existing key that still applies.
5. Run `validate_suite` until it reports no errors, and read its change summary
   (added / updated / deleted).
6. Show the user what will change, then `put_suite`. Use `prune` only when the file is the
   whole script; deleted cases keep their history.

## Areas (sections)

- Group by user-facing capability, not by code module. Most applications need 6–15 areas.
- `code` is 2–4 uppercase letters (`SMK`, `AUT`, `ISO`, `BIL`). It prefixes every key in
  the area and appears in bug reports, so never change it once cases have history.
- Order areas the way a tester should work: smoke first, highest-risk next, cross-cutting
  checks last.
- `note` is one or two sentences: what the area covers, anything to set up first, and how
  much a failure matters.
- Always start with a smoke area (`SMK`): the site loads, health check passes, the build
  version matches what was deployed, sign-in is reachable. Its note should say to stop and
  report the build if any fail.

## Cases

- **One behaviour per case.** If the expected result has two independent outcomes, split it.
- **`key`**: `<CODE>-<NN>`, numbered in order within the area (`ISO-03`). Never renumber or
  reuse a key that has history. If behaviour changes a little, edit the case in place; if
  it changes fundamentally, delete it and add a new key.
- **`title`**: the outcome, in under 80 characters: "Wrong password is rejected", not
  "Test login".
- **`priority`**:
  - `P0` must work; a failure blocks the release. Security, tenant isolation,
    authentication, payments, data loss.
  - `P1` important; a failure needs a decision before release.
  - `P2` lower risk; worth reporting, unlikely to block.
- **`release_focus`**: `true` only for cases covering what the upcoming release changed.
  Clear it once that release has shipped.
- **`setup`**: the state needed before step 1: which account or role, signed in or out,
  what data must exist ("Two organizations, A and B, and an account that belongs only to A").
  Use disposable test accounts and test-mode payment details, never real customer data.
- **`steps`**: 1–6 imperative steps, one action each, using the exact URLs, menu names and
  button labels the tester will see. No code, database access or developer tools unless the
  case is specifically about them (e.g. "no console errors").
- **`expected`**: something observable that can be judged pass or fail. Say what must *not*
  happen as well as what must ("No part of B's record renders, not even briefly"). Never
  write "works correctly".

## Coverage checklist

Cover what applies to the application:

- Smoke and availability
- Authentication and the account lifecycle: sign up, sign in and out, password reset,
  deactivated users
- Roles and permissions: each role can do what it should and is refused what it shouldn't
- Tenant and data isolation: direct links, search, exports and API access across organizations
- Core objects: create, edit, delete or archive and restore, and validation messages that
  name the field and keep other input
- Integrations: email delivery, payments in test mode, file uploads, webhooks, API keys
- The support widget submits a ticket
- Cross-cutting: no console errors, narrow screens, keyboard access, long or unusual input,
  back and refresh during multi-step flows

For every `P0` case where a refusal matters, add the negative case too: signed out, the
wrong role, another tenant, a deactivated user.

## Size

Aim for depth on `P0` and `P1` over breadth in `P2`. A suite a tester can finish in one or
two sessions — typically 60–150 cases — gets run; a 400-case suite gets skimmed.
