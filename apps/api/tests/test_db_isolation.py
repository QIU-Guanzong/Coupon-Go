"""Regression coverage for independent SQLite test-run roots."""
import json
from pathlib import Path
import subprocess
import sys


def test_two_concurrent_processes_keep_same_named_databases_separate(tmp_path):
    # Each process writes the same table and filename with a different value,
    # and both wait until the other process is ready before reading it back.
    script = r'''
import json
from pathlib import Path
import sqlite3
import sys
import time
from tests.db_isolation import sqlite_test_path

barrier = Path(sys.argv[1])
label = sys.argv[2]
db = sqlite_test_path("same-name.db")
with sqlite3.connect(db) as conn:
    conn.execute("CREATE TABLE data (value TEXT NOT NULL)")
    conn.execute("INSERT INTO data VALUES (?)", (label,))
    conn.commit()
    (barrier / ("ready-" + label)).touch()
    deadline = time.monotonic() + 15
    while not all((barrier / ("ready-" + x)).exists() for x in ("one", "two")):
        if time.monotonic() > deadline:
            raise SystemExit("other process did not reach barrier")
        time.sleep(0.01)
    value = conn.execute("SELECT value FROM data").fetchone()[0]
print(json.dumps({"path": db, "value": value}))
'''
    api_root = Path(__file__).resolve().parents[1]
    workers = [subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path), label], cwd=api_root,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    ) for label in ("one", "two")]
    outputs = []
    for worker in workers:
        stdout, stderr = worker.communicate(timeout=25)
        assert worker.returncode == 0, stderr
        outputs.append(json.loads(stdout))
    assert [item["value"] for item in outputs] == ["one", "two"]
    assert outputs[0]["path"] != outputs[1]["path"]
    # TemporaryDirectory removes each process's isolated root on normal exit.
    assert all(not Path(item["path"]).exists() for item in outputs)
