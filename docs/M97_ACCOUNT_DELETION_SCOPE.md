# Account deletion: implemented scope and remaining retention decisions

`POST /api/v3/delete-account` deactivates the authenticated account, replaces
its email with a random unique placeholder, clears the user name and phone,
replaces the password hash with an unusable value, and deletes all OTP rows
for the old address. Search history and favorites are removed; unused wallet
items are voided and their QR code and expiry cleared. Points are zeroed with
a compensating ledger entry. A generic deletion notification is retained,
but the optional free-form reason is deliberately not retained because it
can itself include identifying information.

The old user id and financial/audit records remain intact. `deleted_at` is an
authentication barrier even if `is_active` is accidentally set later. OTP
issue, verification, password reset and deletion serialize on the existing user
row in PostgreSQL. A new registration using the former email is a new user id;
it does not inherit the old account's data or verification records. SQLite
does not provide the same row-lock semantics and is not proof of concurrency
safety for a deployed multi-worker database.

Deletion returns 409 while the user has withdrawable, pending or tracked
cashback, an unreconciled cashback adjustment or a pending or
approved cashback withdrawal, a pending merchant application, a pending
order/refund or unused paid voucher, or while a vendor has an approved shop,
unpaid nonzero statement, unposted nonzero sale or in-progress merchant order.
Shop ownership is checked by `Shop.vendor_id` even after a role change.
Administrators cannot self-delete until their privileges are transferred and
revoked through the existing administrator-management flow. All checks and
changes run in the request transaction.

Authenticated HTTP writes using `get_current_user` or `get_identity_user`
take the account row lock and recheck its active/deleted state before starting.
Some flows commit more than once, background/webhook operations do not all use
these dependencies, and external side effects cannot be undone by this lock.
A complete deletion-vs-order, claim, redemption, refund and webhook audit is
still required before treating the feature as release-ready. A PostgreSQL
delete-vs-password-reset smoke test passed on an earlier revision, before
the shared authentication-dependency lock was added; it does not validate
the final version's complete concurrency behavior. An independent review of
authentication/deletion concurrency was blocked by the tool platform's
security review; do not treat that review as completed or route around it.

This is identity deactivation and limited data removal, **not a claim of
complete erasure from every table, attachment, log or backup**. In particular:

* `cashback_withdrawals.account_name/account_ref` may hold payment identifiers;
  `cashback_appeals.evidence/note`, `refunds.reason`, and failure details may
  contain personal information. They are retained as part of financial or
  dispute records pending an approved retention, access and erasure policy.
* `merchant_applications.contact_name/phone/address/id_card/business_license`
  and other KYC references may contain sensitive identity or business data.
  Pending applications block deletion; historical application evidence is
  not silently destroyed. Access controls, retention basis, actual referenced
  files and a deletion/anonymization schedule still require review.
* Public reviews, review images, tags, historic notifications and operational
  audit logs can contain free text or identifying information. Backups and
  any external processors must be inventoried and addressed separately.

Do not describe this change as complete legal or App Store deletion
compliance until those remaining locations and retention obligations are
audited and a verified process is in place.
