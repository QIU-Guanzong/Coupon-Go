# Isolated interactive trial

This is a separate synthetic-data trial, not Stripe test mode. Do not connect it
to any customer database, payment credentials, uploads or production scheduler.
One browser gets one disposable database; four hours of inactivity or a service
restart resets it. Template preparation must be performed once in a new,
absolute, dedicated state directory. Session databases are local to a single
server process, which must run with exactly one worker.

Set these environment variables in the service environment before preparing the
template and starting the gateway (the example is illustrative, not a shell
script):

- `TRIAL_STATE_DIR`: new absolute state directory owned by the service.
- `DATABASE_URL`: `sqlite:///<TRIAL_STATE_DIR>/template.db`.
- `UPLOAD_DIR`, `MERCHANT_FILE_DIR`: `<TRIAL_STATE_DIR>/uploads` and
  `<TRIAL_STATE_DIR>/merchant-files`, respectively.
- `APP_ENV=dev`, `SEED_ON_STARTUP=0`, `SCHEDULER_ENABLED=0`,
  `STRIPE_CONNECT_ENABLED=1`.
- `SECRET_KEY`: a newly generated random value dedicated to this trial.
- `TRIAL_PUBLIC_ORIGIN` and `CORS_ORIGINS`: the same exact public origin,
  such as `https://example.com`.
- `TRIAL_COOKIE_PATH=/coupongo-trial/`; retain the default secure cookie for
  HTTPS. For local HTTP only, `TRIAL_COOKIE_SECURE=0`.
- Do not configure Stripe, DeepSeek, Redis, or `COUPON_WEB_DIR` in this process.

Set `PYTHONPATH` to include `apps/api` and `infra/interactive-trial`, run
`python infra/interactive-trial/prepare_template.py`, then run
`uvicorn trial_api:app --host 127.0.0.1 --port <private-port> --workers 1`.
Use the locked backend dependencies. The gateway requires its dedicated marked
template and refuses to start on a second worker. It does not run the normal
background scheduler: simulated transfer sweeps are triggered by the related
trial actions within the current browser's database.

For the public UI build, serve static files at `/coupongo/` with
`flutter build web --base-href=/coupongo/ --dart-define=PUBLIC_TRIAL=true
--dart-define=API_BASE_URL=/coupongo-trial`. The existing isolated operator
preview may still serve the assets at `/coupongo-trial/` with that base href.
The API prefix is independent of the static base href and must stay
`/coupongo-trial`; never point this build at the production business API. The reverse proxy must route
`/coupongo-trial/api/v3/...` to the private service as `/api/v3/...`, keep the
browser-visible origin unchanged and permit the same-origin camera for merchant
scanning. Do not reuse the read-only preview's database or static build.

Q仔 district and nearby searches use the browser’s own catalogue and do not call the model.
Free-form chat requires `TRIAL_QBOT_PROXY_URL` set to exactly
`http://127.0.0.1:18760/api/v3/qbot/chat`. This forwards only the request body to
the existing separately controlled Q仔 gateway, subject to its quota and an
additional trial-session rate limit; it never exposes or loads the model key.
Enable only after confirming that its recommendation IDs match the trial seed.
Package comparisons, preference filters, and flash deals run against the current browser’s catalogue and only use its session rate limit. Other free-form answers still use the separate preview catalogue.
When unconfigured or unavailable the API returns an explicit unavailable error.

The acceptance tests are `pytest -q infra/interactive-trial/test_trial_api.py infra/interactive-trial/test_trial_coupon_images.py`
with the backend requirements installed. They exercise isolated browser
sessions and the simulated business flow; real Stripe and real payments are
not tested by this mode.

## Credential-free entry (C3)

Use the isolated interactive trial for credential-free demos. This replaces
the older shared merchant account / empty-login proposal: the production
`POST /login` still requires credentials, and `open_login` in `/app-config`
does not grant access or change the sign-in form.

The Flutter trial opens `POST /api/v3/trial/session` first, then sends the
selected `customer` or `vendor` role to
`POST /api/v3/trial/role`. The direct merchant/customer entry uses the same
endpoint as the identity menu. A visitor may browse without a role token; the public UI opens the
customer session before customer actions. The public gateway no longer issues
or accepts an admin token. It refuses admin routes and payout mutations. Role
changes share data only within the same browser's trial cookie, so a coupon
claimed as a customer can be scanned as a merchant in that browser. Another
browser profile has an independent copy of the data and cannot use the
first browser's role token. Tabs sharing the same cookie are one trial,
not separate users. After an expired or restarted session, reload the trial
page to establish a fresh session, then select a role again.

All money movements in this trial are simulated; do not collect card or real
personal details. The role entry only states that payment does not charge the visitor; it has no
fields requesting personal information. Keep transaction facts where relevant,
without adding a persistent banner to product screens.


## Public customer and merchant actions

The gateway only admits the exact write methods/paths, plus these existing
business operations. Every new write is bound to a role in the current
browser's database, and the application endpoints still enforce object
ownership and their normal status transitions.

- Customer: text-only `POST /reviews`, own review history via `GET /reviews/mine`; wallet `GET /points/exchange-options`
  returns the configured cost and active, free, in-stock, not-yet-held coupons.
  `POST /points/exchange?coupon_id=…` also checks eligibility before the
  ordinary points handler. Isolated `POST /bargain-campaigns/{id}/start`
  records the existing order state without charging money. Help and
  purchase require a distinct customer in the same database and are not
  offered in this browser-isolated version.
- Merchant: coupon preview, submission, field/branch assignment and listing
  lifecycle; package creation, edits and listing lifecycle. Listing and review states still follow
  the existing API configuration. No new approval endpoint or exposed admin route.
- Merchant onboarding accepts only a preset shop choice via the exact
  customer-only application submission route. It accepts no contact data,
  identity numbers, file references or uploads, and creates only an isolated
  pending record. It cannot approve, sign or start payment onboarding.
- Shared uploads, shop contact/branch-profile edits, staff invitations/role
  changes, production login and provider cashier/payout are not offered.
  Customer purchase/redemption/refund receipts from the original isolated
  journey remain supported; they never charge a card or move real funds.

Deployment is an operator step outside this code change. Do not reuse a
production database, enable the background scheduler, or provide credentials
for payment providers. Configure the same-origin cookie and upstream route
before serving the new package at /coupongo/.

### Isolated coupon cover images

- `POST /api/v3/trial/coupon-images`: a signed vendor identity for the
  current browser and an owned shop are required. Send exactly one
  multipart `files` part plus `shop_id`. Accepts verified still JPEG, PNG
  or WebP up to 5 MiB, 4,096 pixels on either edge and 12 million pixels
  total. The gateway stores only resized, metadata-free WebP (maximum
  1,280 pixels on either edge), at most 24 images and 12 MiB per browser.
- `GET /api/v3/trial/coupon-images/{id}.webp` uses the same-origin
  trial cookie, not a bearer in the URL. It is private/no-store and
  disappears with that browser’s trial database on expiry or restart.
  The normal same-origin `/coupongo-trial/api/v3/` proxy includes this
  path; do not mount a shared image directory or serve these bytes
  from another origin. Before any future release, verify the reverse proxy’s
  body-size ceiling for this exact upload endpoint; the gateway’s own cap
  stays at 5 MiB plus multipart framing.
- The upload receipt is accepted only by coupon preview/create/edit,
  for the same browser, merchant and shop. Existing listing rights
  and review status still apply. Shared image uploads, package cover
  reuse, review photos and merchant-document uploads remain unchanged.
  This is a trial-only SQLite table, not a production migration or route.

### Public voucher links (read-only)

Standard voucher details use `#/coupon/{id}-{key}` on the current web entry and
`GET /api/v3/trial/public-coupons/{id}`. This read-only endpoint resolves
public synthetic catalogue offers from the unmodified template even when
the sender edits a private demo copy. It does not require or create a trial
session. An expired or sold-out listing can still show its public terms.
The response provides a `share_key` bound to the offer and shop names.
A recipient resolves the link with `?key=...`, so an old link will not
display a replacement offer if the catalogue later reuses a numeric ID.
Gift links require a shared account and inventory service.

### Public package link (read-only)

- Open `/coupongo/#/p/shu-xiang-hotpot-for-four` in the PUBLIC_TRIAL Web
  build. The existing detail screen displays the reviewed seeded offer.
- `GET /api/v3/trial/public-packages/{slug}` takes no query parameters
  and does not create, read or update a browser session. It resolves one
  allowlisted identity against the dedicated trial template in read-only
  mode. The response has public terms, availability and branch names and
  addresses only; it contains no numeric identities or account fields.
- An unknown, unpublished or out-of-scope identity yields the same 404.
  A previously published sold-out or ended offer may still show its
  terms. Availability and dates refer to the shared template, not to a
  private browser's copied inventory.
- This link does not purchase, link identities after login, or publish
  new merchant-created offers. A shared business catalog and checkout
  handoff require a separate contract. The parent app still initializes
  its existing sandbox session when opened.
