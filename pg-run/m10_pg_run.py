"""M10 Postgres run (pgserver, real Postgres 16).

Mirrors pg-run/p1d_pg_run.py:
  1. start a throwaway Postgres cluster under this directory
     (~/workspace, never /tmp),
  2. create database m10_cash,
  3. `alembic upgrade head` (M10 adds no migration; chain must stay green),
  4. run the M10 test module (+ m96 app-config) against it via
     M10_TEST_POSTGRES_URL.

Run from the repo root of this copy:
  cd ~/workspace/coupon-go-m10 && \
    ~/workspace/coupon-go-p1d/.venv/bin/python pg-run/m10_pg_run.py
Logs to docs/evidence/m10-postgres-run.log in this copy.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
API = os.path.join(REPO, "apps/api")
VENV = "/home/hatch/workspace/coupon-go-p1d/.venv/bin/python"
PGDATA = os.path.join(HERE, "pgdata")
LOG = os.path.join(REPO, "docs", "evidence", "m10-postgres-run.log")


def run(cmd, env=None, cwd=None):
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(f"\n+ {' '.join(cmd)}\n")
        fh.flush()
        r = subprocess.run(cmd, env=env, cwd=cwd, stdout=fh,
                           stderr=subprocess.STDOUT)
    if r.returncode != 0:
        print(f"FAILED ({r.returncode}): {' '.join(cmd)} -- see {LOG}")
        sys.exit(r.returncode)


def main():
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    if os.path.exists(LOG):
        os.remove(LOG)
    sys.path.insert(0, "/home/hatch/workspace/coupon-go-p1d/.venv/lib/"
                        "python3.12/site-packages")
    import pgserver
    server = pgserver.get_server(PGDATA)
    print(f"pgdata={PGDATA}")
    print(f"Postgres: {server.get_uri()}")
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write("# M10 Postgres run (real Postgres via pgserver, "
                 "throwaway cluster; NOT docker-compose db)\n")
        fh.write(f"pgdata={PGDATA}\nPostgres: {server.get_uri()}\n")
    import psycopg2
    conn = psycopg2.connect(server.get_uri())
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute('DROP DATABASE IF EXISTS m10_cash')
    cur.execute('CREATE DATABASE m10_cash')
    cur.close()
    conn.close()
    # Explicit psycopg2 dialect: the venv ships psycopg2-binary, not
    # psycopg (v3), and a bare postgresql:// URL defaults to psycopg.
    db_url = server.get_uri().replace(
        "postgresql://", "postgresql+psycopg2://", 1).replace(
            "/postgres?", "/m10_cash?", 1)
    print(f"db_url: {db_url}")
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(f"db_url: {db_url}\n")
    env = dict(os.environ)
    env.update({"DATABASE_URL": db_url, "COUPON_GO_ENV": "dev"})
    run([VENV, "-m", "alembic", "upgrade", "head"], env=env, cwd=API)
    env["M10_TEST_POSTGRES_URL"] = db_url
    run([VENV, "-m", "pytest",
         "tests/test_m10_cashback_switch.py",
         "tests/test_m96_app_config.py", "-q"], env=env, cwd=API)
    print("M10 POSTGRES RUN: ALL GREEN")
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write("M10 POSTGRES RUN: ALL GREEN\n")


if __name__ == "__main__":
    main()
