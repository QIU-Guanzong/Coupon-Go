"""Read-only public offer resolver with local synthetic gateway fixtures."""
import hashlib
import shutil
import sqlite3

from fastapi.testclient import TestClient

from test_trial_api import gateway  # noqa: F401 - pytest fixture


PATH = '/api/v3/trial/public-packages/'
SLUG = 'shu-xiang-hotpot-for-four'
NAME = '麻辣火鍋四人特價套餐'


def test_same_minimal_package_before_and_after_two_browser_sessions(gateway):
    a, b = TestClient(gateway.app), TestClient(gateway.app)
    session_count = len(list(gateway.SESSIONS.glob('*.db')))
    template_digest = hashlib.sha256(gateway.TEMPLATE.read_bytes()).digest()
    public = a.get(PATH + SLUG)
    assert public.status_code == 200, public.text
    assert public.headers['cache-control'] == 'no-store'
    assert 'set-cookie' not in public.headers
    assert len(list(gateway.SESSIONS.glob('*.db'))) == session_count
    assert public.json() == b.get(PATH + SLUG).json()
    result = public.json()
    assert set(result) == {'slug', 'package'} and result['slug'] == SLUG
    package = result['package']
    assert set(package) == {
        'name', 'shop_name', 'package_price', 'original_price', 'person_count',
        'status', 'valid_from', 'valid_to', 'refund_policy', 'usage_rules',
        'items', 'branches',
    }
    assert package['name'] == NAME
    assert package['package_price'] == 588
    assert package['person_count'] == 4
    assert package['branches'] and set(package['branches'][0]) == {'branch_name', 'address'}
    assert package['status'] == 'active'
    assert package['refund_policy'] == 'non_refundable'

    assert a.post('/api/v3/trial/session').json()['reset']
    assert b.post('/api/v3/trial/session').json()['reset']
    a_path = gateway._session_file(a.cookies.get(gateway.COOKIE))[1]
    b_path = gateway._session_file(b.cookies.get(gateway.COOKIE))[1]
    assert a_path != b_path
    # The same internal identifier now points to unrelated private contents.
    with sqlite3.connect(a_path) as db:
        db.execute("UPDATE packages SET name='Private changed label', package_price=333 WHERE name=?",
                   (NAME,))
    with sqlite3.connect(b_path) as db:
        db.execute("UPDATE packages SET package_price=222 WHERE name=?", (NAME,))
    assert a.get(PATH + SLUG).json() == b.get(PATH + SLUG).json() == result
    assert hashlib.sha256(gateway.TEMPLATE.read_bytes()).digest() == template_digest


def test_unknown_numeric_unlisted_and_sold_out_are_distinct_only_when_public(gateway, tmp_path, monkeypatch):
    isolated_template = tmp_path / 'fixture.db'
    shutil.copy2(gateway.TEMPLATE, isolated_template)
    monkeypatch.setattr(gateway, 'TEMPLATE', isolated_template)
    client = TestClient(gateway.app)
    unknown = client.get(PATH + 'unknown-fixture')
    assert unknown.status_code == client.get(PATH + '2').status_code == 404
    assert unknown.json() == client.get(PATH + '2').json() == client.get(PATH + SLUG + '?price=1').json()
    assert client.post(PATH + SLUG).status_code == 405
    with sqlite3.connect(isolated_template) as db:
        db.execute("UPDATE packages SET stock_left=0 WHERE name=?", (NAME,))
    sold = client.get(PATH + SLUG)
    assert sold.status_code == 200 and sold.json()['package']['status'] == 'sold_out'
    assert sold.json()['package']['package_price'] == 588 and sold.json()['package']['branches']
    with sqlite3.connect(isolated_template) as db:
        db.execute("UPDATE packages SET status='unlisted' WHERE name=?", (NAME,))
    unlisted = client.get(PATH + SLUG)
    assert unlisted.status_code == 404 and unlisted.json() == unknown.json()
    with sqlite3.connect(isolated_template) as db:
        db.execute("UPDATE packages SET status='active', valid_to='2026-10-01', valid_from='2026-09-01' WHERE name=?", (NAME,))
    ended = client.get(PATH + SLUG)
    assert ended.status_code == 200 and ended.json()['package']['status'] == 'expired'
