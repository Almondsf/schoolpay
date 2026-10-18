# Privacy and children's data (NDPA 2023)

SchoolPay processes personal data about children and their guardians on behalf
of schools. Under the Nigeria Data Protection Act 2023 the **school is the data
controller** and SchoolPay is its **data processor**. This page records how the
system meets that responsibility. It is updated as features are added.

## What we store

| About | Data | Why | Protection |
|---|---|---|---|
| Student | Admission number, names, class per term, status | Billing and receipts | School-scoped; staff access by role |
| Student (optional) | Gender, date of birth | Schools' own records | Optional; leave blank if not needed |
| Guardian | Name, relationship to the child | Addressing notices | School-scoped |
| Guardian | Phone, email | Receipts, reminders, parent sign-in | **Encrypted at rest**; looked up by a keyed blind index |
| Guardian | When and how consent was given | NDPA accountability | Recorded on every guardian |

No BVNs, NINs, photos, addresses, religion or health data are collected.

## Principles applied in the design
- **Minimum data.** Only what fee collection needs. Gender and date of birth
  are optional.
- **Encryption.** Guardian phone and email (and staff MFA secrets) are
  encrypted at rest (`app/core/crypto.py`). Lookups use a keyed HMAC of the
  normalised phone, so the database never holds phone numbers in plain text.
- **Consent.** Staff must confirm the school holds the guardian's consent
  before guardian details are saved, whether one at a time or by spreadsheet
  import. The time and method are recorded on the guardian.
- **Audit.** Changes to a guardian's contact details are audited without
  storing the numbers themselves, because the phone controls parent-portal
  access.
- **Logs.** Logs never contain OTPs, tokens, BVNs or full account numbers
  (a redaction filter enforces this).
- **Isolation.** Each school's data is visible only to that school's staff and
  to the guardians linked to each child. A parent with children at two schools
  is a separate guardian record in each; the schools never see each other's records.

## Receipts
Printed and PDF receipts carry the student's name, admission number and class,
because they're given to the parent. The public verification page behind the
QR code shows only the school, receipt number, amount, date, status and the
student's initials.

## Parent portal
Parents sign in with the phone number the school holds for them. We store a
hash of each sign-in code for at most a day, and their portal sessions for 30
days after they end. A parent sees only their own children. Sign-in codes are
sent by SMS through Termii (a processor).

## Shared with payment gateways
To issue a student's account number, the gateway (Paystack) receives the
student's first and last name, the primary guardian's phone number, and the
guardian's email (or a routing address we generate if there isn't one). Card
payments send the payer's email to the gateway. Gateways are processors under
their own NDPA obligations. No BVNs are sent or stored, and card details never
reach SchoolPay.

## Still to build (tracked in the roadmap)
- Guardian consent records, data export and erasure, retention rules (chunk 10).
- NDPC registration and a Data Processing Agreement template for schools.
