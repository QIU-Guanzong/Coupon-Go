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

Serve a web build under `/coupongo-trial/` with
`--base-href=/coupongo-trial/ --dart-define=PUBLIC_TRIAL=true
--dart-define=API_BASE_URL=/coupongo-trial`. The reverse proxy must route
`/coupongo-trial/api/v3/...` to the private service as `/api/v3/...`, keep the
browser-visible origin unchanged and permit the same-origin camera for merchant
scanning. Do not reuse the read-only preview's database or static build.

Q仔 is unavailable unless `TRIAL_QBOT_PROXY_URL` is set to exactly
`http://127.0.0.1:18760/api/v3/qbot/chat`. This forwards only the request body to
the existing separately controlled Q仔 gateway, subject to its quota and an
additional trial-session rate limit; it never exposes or loads the model key.
Enable only after confirming that its recommendation IDs match the trial seed.
When unconfigured or unavailable the API returns an explicit unavailable error.

The acceptance tests are `pytest -q infra/interactive-trial/test_trial_api.py`
with the backend requirements installed. They exercise isolated browser
sessions and the simulated business flow; real Stripe and real payments are
not tested by this mode.

## Credential-free entry (C3)

Use the isolated interactive trial for credential-free demos. This replaces
the older shared merchant account / empty-login proposal: the production
`POST /login` still requires credentials, and `open_login` in `/app-config`
does not grant access or change the sign-in form.

The Flutter trial opens `POST /api/v3/trial/session` first, then sends the
selected `customer`, `vendor`, or `admin` role to
`POST /api/v3/trial/role`. The direct merchant/customer entry uses the same
endpoint as the identity menu. A guest can browse without a role token. Role
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
