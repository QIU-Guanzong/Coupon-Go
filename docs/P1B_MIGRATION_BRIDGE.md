# P1-B migration bridge

`0021_p1b_stripe_connect` was originally based on `0019_invoice_credits`,
independently of `0020_m92_legal_consents`. A database may have either or both
as recorded heads. `0022_p1b_forward_bridge` depends on both, so Alembic runs
the missing parent and then converges on one head. The graph deliberately keeps
the historical fork: a single head does not make its history strictly linear.

Do not rewrite 0021 to depend on 0020 for databases that may have executed the
old 0021. Alembic then assumes 0020 ran, and can advance to the new head even
though `legal_acceptances` is absent. A linear graph is safe only for a new
queue that can prove no database ever executed that older revision. Do not
stamp an existing database to the bridge or change its revision row to skip a
migration. Any future change of these revision numbers must preserve the
existing revision identities and ancestry.

The bridge checks for existing compatible columns before adding missing ones.
It does not backfill historical settlement allocations, refund holds,
commission snapshots or dispatch markers. A NULL dispatch marker means the
historical state is unknown, not proof a request was never made. Existing
incompatible types or nullability stop the bridge before it issues DDL.

The bridge is forward-only: its downgrade raises an error even when all values
are NULL because the columns may have existed before the bridge, and their
provenance is unavailable. Recovering a pre-upgrade database requires a
coordinated, verified restore and reconciliation of any subsequently recorded
payments or refunds. Take and verify an appropriate backup before deployment.

Run `apps/api/tests/test_p1b_forward_bridge.py` for fresh and historical upgrade
fixtures. PostgreSQL cases additionally require `MIGRATION_BRIDGE_PG_URL` to
refer to a disposable local test cluster; they create separate uniquely named
databases. These are test fixtures, not a production rollout procedure.
