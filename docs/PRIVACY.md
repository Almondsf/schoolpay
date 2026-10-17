# Privacy and children's data (NDPA 2023)

SchoolPay processes personal data about children and their guardians on behalf
of schools. Under the Nigeria Data Protection Act 2023 the **school is the data
controller** and SchoolPay is its **data processor**. This page records how the
system meets that responsibility. It is updated as features are added.

## Principles applied in the design
- **Minimum data.** Only what fee collection needs: student name, class,
  admission number, and guardian name and phone. No BVNs are stored.
- **Encryption.** Sensitive fields are encrypted at rest (`app/core/crypto.py`).
- **Logs.** Logs never contain OTPs, tokens, BVNs or full account numbers
  (a redaction filter enforces this).
- **Isolation.** Each school's data is visible only to that school's staff and
  to the guardians linked to each child.

## Still to build (tracked in the roadmap)
- Guardian consent records, data export and erasure, retention rules (chunk 10).
- NDPC registration and a Data Processing Agreement template for schools.
