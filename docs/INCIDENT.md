# Incident runbook

Two sequences with clocks on them. Keep this file current; a runbook that
was never rehearsed is a hope. Dates below were checked on 2026-09-29 against
secondary summaries where marked; confirm against the primary text before
relying on a deadline.

## A leaked key or credential

Order matters. Do not wait to confirm exposure before step 1.

1. Revoke or rotate the credential at the provider. Assume the old value
   stays live until the provider confirms otherwise.
2. Redeploy the new value everywhere it is consumed: server secrets,
   CI variables, phones, cron, partner webhooks. Note every consumer touched.
3. Read the provider's audit log for the exposure window; record what was
   accessed. This decides whether step 6 applies.
4. Remove the value from the working tree and commit through the gate.
5. Decide on a history rewrite. Once rotated, a rewrite is often
   unnecessary and always destructive. If required: fresh clone,
   `git filter-repo --sensitive-data-removal`, `git push --force --mirror`,
   a GitHub Support ticket to purge cached views, then re-clone every machine
   and worktree.
6. If personal data may have been accessed, the breach clocks below start.
7. Write the incident down: what leaked, when, how it was found, what was
   rotated, what the logs showed, what changed to stop a repeat.

Per-provider revocation facts (where to revoke, whether the old key dies
instantly, what else a rotation breaks) live in the private operations
notes with a "verified on" date, not here: they change, and a stale promise
here is worse than none.

## A data breach

This library holds no personal data. This section applies to the products
that run its deploy and backup code; each keeps its own copy.

Clocks that run from the moment the breach is known. Where two apply, the
shortest one governs the first notification.

| Regime | Applies when | Clock | Status |
| --- | --- | --- | --- |
| CERT-In directions (India, 28 Apr 2022) | Any Indian entity; listed incident types | Report within 6 hours; keep ICT logs 180 days in India | In force |
| GDPR Art. 33 / UK GDPR | EU or UK residents' personal data | Supervisory authority within 72 hours; affected people without undue delay when high risk | In force |
| EU Cyber Resilience Act | Software products with digital elements on the EU market (mobile apps at least; SaaS scope unconfirmed) | Early warning 24 h, notification 72 h, final report 14 days (actively exploited vulnerability) or 1 month (severe incident), via the ENISA platform | Reporting duties from 11 Sep 2026 (secondary sources) |
| India DPDP Act + Rules (notified 13 Nov 2025) | Indian data principals | Notify the Board and affected principals without delay; detailed report within 72 hours; logs kept at least 1 year | Substantive duties from 13 May 2027 (secondary sources) |

Steps:

1. Contain: revoke credentials, isolate the system, preserve logs.
2. Scope: which records, which people, which regions, from when.
3. Notify on the shortest applicable clock above; use the templates in the
   private operations notes; one named owner sends every notice.
4. Tell affected people plainly what happened, what they should do, and how
   to reach the privacy contact.
5. Post-incident: root cause, fix, and a dated entry in docs/DECISIONS.md.

Rehearse this once a year with a tabletop run and record the date here.
