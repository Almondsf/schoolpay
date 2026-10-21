# Handover: where the project stands and how to pick it up

For a new Claude Code session (or anyone) taking over ParentPay. Read in this order:

1. `CLAUDE.md`: the rules of the project (loaded automatically).
2. **This file**: what's built since the original roadmap, the owner's decisions, what's next, and the
   quirks of this machine.
3. `docs/SYSTEM_OVERVIEW.md`: how everything works (the developer reference; kept current).
4. `OWNER_TODO.md`: everything waiting on the owner (accounts, keys, decisions).
5. `PLAN.md`: the original product plan and roadmap (chunks 0 to 10, all done).

Last updated: 2026-10-20, at commit `edb7106` on `main`.

## Where things stand

The roadmap (PLAN.md chunks 0 to 10) is finished. Since then, at the owner's request:

| When | What | Where to look |
|---|---|---|
| 19 Oct | Payment plans (pay a bill in instalments) | `fees/plans.py` |
| 19 Oct | Extra fees for whole classes mid-term, pupil exemptions, a price list, one-off charges per pupil | `fees/charges.py` |
| 19 Oct | Product named **ParentPay** (one setting: `APP_NAME` / `VITE_APP_NAME`; the code keeps the codename `schoolpay`) | `core/config.py`, `frontend/src/lib/brand.ts` |
| 19 Oct | "School at a glance" (pupils, classes, records needing attention), now under the money figures on the dashboard | `students/summary.py`, `console/Dashboard.tsx` |
| 19 Oct | **Family payment link**: one link per parent, no sign-in, pay for all children with the amount chosen per child | `payments/family.py`, `family_links.py`, `public/FamilyPay.tsx` |
| 19 Oct | **Phone calls** for parents who don't read messages (English or Pidgin; overdue reminders and payment confirmations; Africa's Talking) | `notifications/voice.py` |
| 19 Oct | Pay for all children by bank transfer, card or USSD on the gateway's checkout, also from the portal ("Pay for all your children") | `payments/family.py`, `parent/ParentApp.tsx` |
| 20 Oct | **Fee status by pupil** download (every fee paid / part paid / unpaid, all classes or one; Excel with a sheet per class, PDF) | `reports/fee_status.py`, `reports/exports.py` |
| 20 Oct | Payment provider **switchable at runtime** (Platform → Providers); **Bachs** adapter built but not in use | `payments/providers.py`, `gateways/bachs.py` |
| 20 Oct | **Paystack chosen** as the provider; its sandbox works locally (real checkout page) | `CLAUDE.md`, `docs/DEPLOYMENT.md` |
| 20 Oct | **School admins can do everything a bursar can** (with two-factor sign-in); only the proprietor manages staff and the bank account | `accounts/permissions.py` |
| 20 Oct | **Fee breakdowns** wherever a balance shows (staff pupil page, portal, payment page) | `fee_status.fee_breakdowns`, `lib/FeeBreakdown.tsx` |
| 20 Oct | Fees added to the fee grid after billing can be added to the bills already sent (Fees page offers it) | `charges.sync_bills` |
| 20 Oct | **Pay for particular fees** (tick WAEC on the payment page or the cash form); earmarked money goes to those fees first, even before older debts | `payments/earmarks.py`, `PaymentEarmark` |
| 20 Oct | **Deployed**: API, worker, Postgres and Redis on Render (https://schoolpay-a97t.onrender.com), web app on Netlify (https://parentpay.netlify.app), which forwards `/api` to Render | `docs/DEPLOYMENT.md` ("On Render and Netlify"), `frontend/public/_redirects` |
| 21 Oct | Production logs leave out database query values; startup refuses a malformed Redis address and never echoes a rejected setting's value | `core/db.py`, `core/config.py` |
| 21 Oct | Fee status download per class: each class lists only its own fees, part-paid fees show paid and left, the PDF has a page per class | `reports/exports.py` (`fee_status_pdf`) |
| 21 Oct | **Redesign**: design tokens + Plus Jakarta Sans + lucide icons, a fixed sidebar (Setup always open), a one-screen dashboard with a tabbed panel, the pupil page as a profile header with tabs; shared `Tabs`, `Stat`, `Menu`, `BackLink` in `lib/ui.tsx` | `frontend/src/styles.css`, `console/ConsoleApp.tsx` |
| 21 Oct | **Classes from the usual list**: Creche to SSS 3 ticked for a new school, untick/add own, one batch; missing ones slot into the promotion order later | `schools/academics_service.py` (`STANDARD_CLASSES`, `add_class_levels`), `console/setup/ClassSetup.tsx` |
| 21 Oct | **School year filled in** (usual Nigerian term dates from the Lagos date, today's term made current) | `schools/academics_service.py` (`suggest_session`), `console/setup/Calendar.tsx` |
| 21 Oct | **Printable pay cards** (one pupil or a class, four to an A4 page: account number + this term's fees with what's left, total to pay; no QR code); Lagos time in one place (`core/clock.py`) | `payments/paycards.py`, `console/payments/PayCards.tsx` |
| 21 Oct | Real `favicon.ico` + PNG icons (Apple touch, 192/512 in `site.webmanifest`): Netlify's app fallback was answering `/favicon.ico` with the page, so Safari and some tabs showed no icon | `frontend/public/` |

Also made: a pitch deck for school proprietors (a claude.ai artifact, private to the owner:
https://claude.ai/artifact/U8F5XpU55wtrACtihHp2oH; the dashboard figures and contact details are
placeholders) and the Word overview `docs/product/ParentPay - what it does.docx`.

## How the owner wants work done

These are standing rules from the owner. CLAUDE.md has the project rules; these sit on top.

- **Branch, CI, merge.** Branch off `main`, commit, push, wait for **both** GitHub Actions workflows
  (Backend, Frontend) to pass, then fast-forward `main` and delete the branch. One commit per piece of
  work; describe the feature, never the word "chunk"; **no AI attribution** of any kind. The owner
  sometimes merges a PR on GitHub himself: fetch first and build on what's there.
- **Never a database call inside a loop.** Batch with `IN (...)`, joins, bulk inserts, or one locked
  `SELECT … FOR UPDATE`. Services prove it with "fixed number of statements" tests (`QueryCounter`).
- **Docs are part of every change**: `docs/SYSTEM_OVERVIEW.md`, `docs/USER_STORIES.md`, and
  `SECURITY.md` / `PRIVACY.md` / `DEPLOYMENT.md` / `.env.example` when relevant.
- **Word overview**: when a feature ships, update `docs/product/overview.cjs` (rows, the dated CHANGES
  line, UPDATED) and rebuild with `cd docs/product && npm run build`. If the owner has the .docx open
  the build fails with EBUSY: ask them to close it. To look at the layout, export to PDF through Word
  (PowerShell `Word.Application`, SaveAs format 17) and stop any **windowless** WINWORD process
  afterwards (never one with a window: that's the owner's).
- **`OWNER_TODO.md`**: anything only the owner can do goes there in the same piece of work, marked
  _(new)_, and is mentioned in the summary.
- **Tests**: run only the new and affected test files, never the full suites (slow on this machine).
  Check frontend tests under Node 22 as CI does:
  `npx -y -p node@22 node node_modules/vitest/vitest.mjs run <paths>`. Run the affected Playwright
  specs with `PW_CHANNEL=msedge npx playwright test <names>`.
- **Frontend formatting**: there is no Prettier config. Never run bare `npx prettier --write`. Match the
  existing style (single quotes, 2 spaces, lines up to ~140).
- **Decisions that change security or money rules**: ask the owner first (a short multiple-choice
  question works well), and record the answer in the code comment and the docs.
- **Talking to the owner**: plain language, what changed and what they can now do, what's waiting on
  them. They're not a developer and often test in the browser themselves.
- **Comments**: the owner asked for unnecessary comments to be removed, then paused it (see "Next").
  Meanwhile don't add new `# --- Section ---` divider lines; keep comments to the non-obvious why.

## Owner decisions (with dates)

- 2026-10-18 Pricing model: set-up fee + yearly licence + per pupil per term, free for parents (prices in
  the platform plan are defaults: ₦50,000 / ₦100,000 / ₦500).
- 2026-10-19 Product name "ParentPay" for now (trademark to be checked: it's a UK company's brand).
- 2026-10-19 Email through Zoho Mail; WhatsApp and voice via Zoho if it can (otherwise Termii / Africa's Talking).
- 2026-10-19 A family payment can be split however the parent likes; nothing is split automatically.
- 2026-10-20 Parents keep signing in with a texted code (the owner considered dropping it; kept for
  children's data protection).
- 2026-10-20 Paystack is the payment provider; Bachs stays built but unconfigured (it can't give each
  pupil an account number).
- 2026-10-20 School admins get everything a bursar has, with two-factor sign-in.
- 2026-10-20 Money a parent marks for a fee goes to that fee even if older debts are owed.
- 2026-10-21 Pay cards list the term's fees and what's left to pay; no QR code.
- 2026-10-21 A fee added to a class in the fee grid goes onto every bill in that class already sent, as soon as it's saved (no confirmation).
- 2026-10-21 Money not marked for particular fees covers tuition first, then the bill's other fees in order.

## Next: what's open, roughly in order

1. **Card payment of school subscriptions** (step 3 of the plan agreed on 19 Oct): schools pay their
   ParentPay bill by card/transfer through Paystack, not only by bank transfer recorded by hand.
2. **Parent consent in the portal** (step 4): a parent confirms consent themselves on first sign-in
   (`ConsentMethod.PORTAL` already exists).
3. **The "later" list in PLAN.md** (step 5): only when the owner says so.
4. From the low-literacy discussion, still open: a short "number-first" text style for messages
   (the printable pay card is done).
5. **Comment cleanup**: the owner asked to remove unnecessary comments and trim long docstrings
   (choice: "noise + trim docstrings"), then said "abort" after section dividers were removed. That
   pass is saved in `git stash` ("comment cleanup pass 1 … aborted 2026-10-19"); it predates a lot of
   later work, so redo it fresh rather than applying the stash. **Ask before resuming.**
6. Yoruba, Hausa and Igbo voice calls need recorded voices (owner to arrange).

Waiting on the owner (details in `OWNER_TODO.md`): Paystack "Dedicated NUBAN" approval (per-pupil
account numbers; until then parents pay through checkout), live keys, WhatsApp/SMS/voice accounts,
actual prices, legal documents, a pilot school.

## Running it on this machine

The scratch Postgres is on **5433** (`pg_ctl -D /c/Users/HP/.schoolpay-scratch/pgdata -o "-p 5433" …`
start; see CLAUDE.md). API on **8002**, Vite on **5175**. Never use the owner's own database on 5432.

```bash
cd backend && .venv/Scripts/python -m app.core.migrate && .venv/Scripts/uvicorn app.main:app --port 8002 --reload
cd frontend && npx vite --port 5175 --strictPort
cd backend && .venv/Scripts/python -m app.cli seed-demo --reset     # rebuild the demo school (scratch DB only)
```

Local `backend/.env` (never committed) has, beyond `.env.example`: `PAYMENT_GATEWAYS=paystack,fake`, the
owner's **Paystack test key** (`PAYSTACK_SECRET_KEY=sk_test_…`), `ENFORCE_MFA=false` (so the school admin
signs in without a code locally; production refuses it) and `SMS_BACKEND=memory`.

**Demo accounts** (password `demo-password-123`, authenticator setup key `JBSWY3DPEHPK3PXP`):
`admin@demo.schoolpay.ng` (Ada Obi, school admin; no code locally), `bursar@…` and `proprietor@…`
(code required), `platform@…` (platform admin). Demo parent: phone **0803 500 0000**; the texted code is
readable locally at `http://localhost:8002/api/v1/dev/sms/latest?to=08035000000`. Paystack test card:
4084 0840 8408 4081, any future expiry, CVV 408, PIN 0000, OTP 123456.

**After every `seed-demo --reset`**, reconnect the demo school to the Paystack sandbox. Paystack's test
mode allows only 3 bank lookups a day, so link the test subaccount already created (scratch DB only):

```python
# backend/.venv/Scripts/python - <<'EOF'
import uuid
from sqlalchemy import select
from app.core.db import SessionLocal
from app.core.tenancy import scope_platform_wide, scope_to_school
from app.demo import DEMO_SLUG
from app.modules.schools.models import School
from app.modules.payments.models import SchoolPaymentAccount
from app.modules.payments.gateways import GatewayCode
with SessionLocal() as s:
    scope_platform_wide(s); school_id = s.scalar(select(School.id).where(School.slug == DEMO_SLUG))
with SessionLocal() as s:
    scope_to_school(s, school_id)
    s.add(SchoolPaymentAccount(id=uuid.uuid4(), gateway=GatewayCode.PAYSTACK, subaccount_code="ACCT_jrloeq5ijkp1lqc",
        bank_code="057", bank_name="Zenith Bank", account_number="0000000000", account_number_last4="0000",
        account_name="Test", platform_fee_bp=100))
    s.commit()
# EOF
```

## Quirks learned the hard way

- **Restart the servers after code changes.** Uvicorn's `--reload` and Vite's hot reload miss changes on
  this Windows machine fairly often. A stopped Vite can leave an orphan `node …vite.js` holding port 5175:
  check its command line (`Get-CimInstance Win32_Process`) before stopping it.
- **CI path filters**: the Backend workflow also runs when `frontend/openapi.json` changes, and both can be
  started by hand. After any API change, regenerate the types
  (`python -m app.openapi > ../frontend/openapi.json && npm run gen:api`) or the Backend job fails.
  `openapi.json` shows as different locally only because of CRLF line endings.
- **Playwright selectors**: match menu links and headings with `exact: true`; the dashboard grows and
  substrings collide ("Students" vs "60 students owe", "By class" vs "Pupils by class").
- **Tests run inside one transaction**, so `now()` is the same for every row: order by something else
  (receipt numbers, ids) when a test needs a stable order.
- **Docker Desktop doesn't work here**; Dockerfiles are checked by CI only.
- **Bare `python`** on PATH belongs to another project; use `py -3.12` or `backend/.venv/Scripts/python`.
- **Shell heredocs with apostrophes** sometimes break in this Git Bash; write edit scripts to a file in
  the scratchpad and run them.
- **Paystack test mode**: subaccounts work with Zenith (057) + `0000000000`; "Dedicated NUBAN" isn't
  enabled on the owner's business yet; checkout pages sit behind a Cloudflare check that blocks automated
  browsers (fine for people).
