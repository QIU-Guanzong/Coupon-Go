"""Deletion security regressions against isolated databases."""
import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault('APP_ENV', 'dev')
os.environ.setdefault('SEED_ON_STARTUP', '0')
os.environ.setdefault('SCHEDULER_ENABLED', '0')

from app import models
from app.database import Base, get_db
from app.main import app


@pytest.fixture
def case(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'security.db'),
                           connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)

    def db_override():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = db_override
    with TestClient(app) as client:
        yield client, sessions
    app.dependency_overrides.pop(get_db, None)
    engine.dispose()


def register(client, email, password='First-Pass-123', name='Name Before', phone='12345678', role='customer'):
    result = client.post('/api/v3/new-register', json={
        'email': email, 'password': password, 'name': name, 'phone': phone, 'role': role})
    assert result.status_code == 200, result.text
    code = client.get('/api/v3/_dev/otp', params={'email': email}).json()['code']
    result = client.post('/api/v3/new-verify-otp', json={'email': email, 'code': code, 'purpose': 'register'})
    assert result.status_code == 200, result.text
    return result.json(), code


def token(data):
    return {'Authorization': 'Bearer ' + data['access_token']}


def test_deletion_removes_identity_invalidates_old_otp_and_allows_fresh_registration(case, monkeypatch):
    from app.routers import auth
    client, sessions = case
    email = 'deleted-person@example.com'
    old, old_register_code = register(client, email)
    with sessions() as db:
        old_id = old['user_id']
        old_hash = db.get(models.User, old_id).password_hash
        db.add(models.OtpCode(email=email, code='314159', purpose='forgot',
                              expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=10)))
        db.commit()
    assert client.post('/api/v3/delete-account', headers=token(old), json={}).status_code == 200
    with sessions() as db:
        row = db.get(models.User, old_id)
        assert row.deleted_at is not None and row.is_active is False
        assert row.email != email and row.name in ('', None) and row.phone in ('', None)
        assert row.password_hash != old_hash
        assert db.query(models.OtpCode).filter_by(email=email).count() == 0
    assert client.get('/api/v3/me', headers=token(old)).status_code == 401
    assert client.post('/api/v3/login', json={'email': email, 'password': 'First-Pass-123'}).status_code == 401
    assert client.post('/api/v3/password/reset', json={'email': email, 'code': '314159', 'new_password': 'Stolen-456'}).status_code != 200

    # Avoid a random six-digit collision obscuring which account issued a code.
    next_code = '111111' if old_register_code != '111111' else '222222'
    monkeypatch.setattr(auth.random, 'choices', lambda _digits, k: list(next_code))
    fresh = client.post('/api/v3/new-register', json={'email': email, 'password': 'Fresh-Pass-456', 'name': 'New', 'role':'customer'})
    assert fresh.status_code == 200, fresh.text
    assert client.post('/api/v3/password/reset', json={'email': email, 'code': '314159', 'new_password': 'Stolen-456'}).status_code != 200
    # A code from the removed identity must never verify the new account.
    assert client.post('/api/v3/new-verify-otp', json={'email': email, 'code': old_register_code, 'purpose':'register'}).status_code != 200
    new_code = client.get('/api/v3/_dev/otp', params={'email': email}).json()['code']
    verified = client.post('/api/v3/new-verify-otp', json={'email': email, 'code': new_code, 'purpose':'register'})
    assert verified.status_code == 200, verified.text
    assert verified.json()['user_id'] != old_id
    assert client.get('/api/v3/me', headers=token(old)).status_code == 401
    assert client.post('/api/v3/login', json={'email': email, 'password': 'First-Pass-123'}).status_code == 401
    assert client.post('/api/v3/login', json={'email': email, 'password': 'Fresh-Pass-456'}).status_code == 200


def test_resend_does_not_reactivate_deleted_identity(case):
    client, sessions = case
    email = 'again@example.com'
    old, _ = register(client, email)
    assert client.post('/api/v3/delete-account', headers=token(old), json={}).status_code == 200
    assert client.post('/api/v3/new-resent-otp', json={'email':email, 'purpose':'register'}).status_code == 200
    # Unknown/deleted identity returns the same response but must not issue OTP.
    with sessions() as db:
        assert db.query(models.OtpCode).filter_by(email=email, used=False).count() == 0
        old_row = db.get(models.User, old['user_id'])
        assert old_row.deleted_at is not None and not old_row.is_active
    assert client.post('/api/v3/login', json={'email':email, 'password':'First-Pass-123'}).status_code == 401


def test_deleted_account_cannot_be_reactivated_through_resend_and_verify(case):
    client, sessions = case
    email = 'revival@example.com'
    old, _ = register(client, email)
    assert client.post('/api/v3/delete-account', headers=token(old), json={}).status_code == 200
    result = client.post('/api/v3/new-resent-otp', json={'email':email, 'purpose':'register'})
    assert result.status_code == 200
    with sessions() as db:
        otp = db.query(models.OtpCode).filter_by(email=email, purpose='register', used=False).order_by(models.OtpCode.id.desc()).first()
        code = otp.code if otp else '000000'
    result = client.post('/api/v3/new-verify-otp', json={'email':email, 'code':code, 'purpose':'register'})
    assert result.status_code != 200
    assert client.post('/api/v3/login', json={'email':email, 'password':'First-Pass-123'}).status_code == 401


def test_deleted_at_is_a_hard_auth_barrier_even_if_active_is_accidentally_set(case):
    client, sessions = case
    email = 'deleted-flag@example.com'
    old, _ = register(client, email)
    with sessions() as db:
        row = db.get(models.User, old['user_id'])
        row.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        row.is_active = True
        db.add(models.OtpCode(email=email, code='271828', purpose='register',
                              expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=10)))
        db.commit()
    assert client.get('/api/v3/me', headers=token(old)).status_code == 401
    assert client.post('/api/v3/login', json={'email':email, 'password':'First-Pass-123'}).status_code == 401
    assert client.post('/api/v3/new-verify-otp', json={'email':email, 'code':'271828', 'purpose':'register'}).status_code != 200
    assert client.post('/api/v3/resent-otp', json={'email':email, 'purpose':'forgot'}).status_code == 200
    assert client.post('/api/v3/password/forgot', json={'email':email}).status_code == 200
    with sessions() as db:
        assert db.query(models.OtpCode).filter_by(email=email, purpose='forgot').count() == 0


def test_inflight_cashback_withdrawal_blocks_deletion(case):
    from app.models_cashback import CashbackWithdrawal
    client, sessions = case
    old, _ = register(client, 'withdrawal@example.com')
    with sessions() as db:
        db.add(CashbackWithdrawal(user_id=old['user_id'], amount=25, method='fps',
                                  account_name='example', status='pending'))
        db.commit()
    result = client.post('/api/v3/delete-account', headers=token(old), json={})
    assert result.status_code == 409
    with sessions() as db:
        assert db.get(models.User, old['user_id']).deleted_at is None


def test_vendor_deletion_guard_covers_shops_settlements_and_open_orders(case):
    from app.models_packages import Order, Package
    from app.models_line_f import Settlement
    client, sessions = case
    old, _ = register(client, 'vendor-owner@example.com', role='vendor')
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with sessions() as db:
        shop = models.Shop(vendor_id=old['user_id'], name='Demo shop', is_approved=True)
        db.add(shop)
        db.flush()
        shop_id = shop.id
        db.commit()
    assert client.post('/api/v3/delete-account', headers=token(old), json={}).status_code == 409
    with sessions() as db:
        db.get(models.Shop, shop_id).is_approved = False
        settlement = Settlement(shop_id=shop_id, period='2026-01', period_start=now,
                                period_end=now, net_amount=20, status='pending')
        db.add(settlement)
        db.commit()
        settlement_id = settlement.id
    assert client.post('/api/v3/delete-account', headers=token(old), json={}).status_code == 409
    with sessions() as db:
        db.get(Settlement, settlement_id).status = 'paid'
        buyer = models.User(email='order-buyer@example.com', password_hash='!', role='customer', is_active=True)
        package = Package(shop_id=shop_id, name='Offer', original_price=10, package_price=8,
                          valid_from=now, valid_to=now + timedelta(days=1), stock_total=1, stock_left=1)
        db.add_all([buyer, package])
        db.flush()
        order = Order(user_id=buyer.id, package_id=package.id, amount=8, payment_status='pending')
        db.add(order)
        db.commit()
        order_id = order.id
    assert client.post('/api/v3/delete-account', headers=token(old), json={}).status_code == 409
    with sessions() as db:
        db.get(Order, order_id).payment_status = 'cancelled'
        db.commit()
    assert client.post('/api/v3/delete-account', headers=token(old), json={}).status_code == 200
    with sessions() as db:
        assert db.get(models.User, old['user_id']).deleted_at is not None
        assert db.get(Settlement, settlement_id).status == 'paid'
        assert db.get(Order, order_id).payment_status == 'cancelled'


def test_unposted_merchant_receivable_blocks_delete_until_sale_is_revoked(case):
    client, sessions = case
    old, _ = register(client, 'merchant-sales@example.com', role='vendor')
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with sessions() as db:
        shop = models.Shop(vendor_id=old['user_id'], name='Former shop', is_approved=False)
        buyer = models.User(email='buyer-sale@example.com', password_hash='!', role='customer', is_active=True)
        db.add_all([shop, buyer]); db.flush()
        coupon = models.Coupon(shop_id=shop.id, vendor_id=old['user_id'], coupon_name='Paid item',
                               coupon_type='Price', discount_price=30, valid_from=now,
                               valid_to=now + timedelta(days=1), total=1, remaining=0)
        db.add(coupon); db.flush()
        item = models.WalletItem(user_id=buyer.id, coupon_id=coupon.id, status=2)
        db.add(item); db.flush()
        sale = models.Redemption(wallet_item_id=item.id, coupon_id=coupon.id, shop_id=shop.id,
                                 scanned_by_vendor_id=old['user_id'], face_value=30)
        db.add(sale); db.commit(); sale_id = sale.id
    assert client.post('/api/v3/delete-account', headers=token(old), json={}).status_code == 409
    with sessions() as db:
        assert db.get(models.User, old['user_id']).deleted_at is None
        db.get(models.Redemption, sale_id).revoked_at = now
        db.commit()
    assert client.post('/api/v3/delete-account', headers=token(old), json={}).status_code == 200
    with sessions() as db:
        assert db.get(models.Redemption, sale_id) is not None


def test_customer_paid_unused_voucher_blocks_delete(case):
    from app.models_packages import Order, Package
    client, sessions = case
    old, _ = register(client, 'customer-with-order@example.com')
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with sessions() as db:
        vendor = models.User(email='vendor-order@example.com', password_hash='!', role='vendor', is_active=True)
        db.add(vendor); db.flush()
        shop = models.Shop(vendor_id=vendor.id, name='Shop', is_approved=True)
        db.add(shop); db.flush()
        package = Package(shop_id=shop.id, name='Package', original_price=20, package_price=15,
                          valid_from=now, valid_to=now + timedelta(days=1), stock_total=1, stock_left=0)
        db.add(package); db.flush()
        order = Order(user_id=old['user_id'], package_id=package.id, amount=15, paid_amount=15,
                      payment_status='paid', refund_status='none', settlement_status='pending')
        coupon = models.Coupon(shop_id=shop.id, vendor_id=vendor.id, coupon_name='Paid voucher',
                               coupon_type='Price', kind='package', valid_from=now,
                               valid_to=now + timedelta(days=1), total=1, remaining=0)
        db.add_all([order, coupon]); db.flush()
        item = models.WalletItem(user_id=old['user_id'], coupon_id=coupon.id, order_id=order.id,
                                 source='purchase', status=1)
        db.add(item); db.commit()
    result = client.post('/api/v3/delete-account', headers=token(old), json={})
    assert result.status_code == 409
    assert result.json()['code'] == 'unused_paid_voucher'
    assert '請先退款或使用' in result.json()['detail']
    with sessions() as db:
        assert db.get(models.User, old['user_id']).deleted_at is None
        assert db.query(models.WalletItem).filter_by(user_id=old['user_id']).one().status == 1


@pytest.fixture
def postgres_case():
    """Exclusive socket-only test cluster, never a production connection."""
    from pathlib import Path
    from sqlalchemy import URL

    if not os.environ.get('COUPONGO_M97_PG_SOCKET'):
        pytest.skip('isolated PostgreSQL socket not supplied')
    socket = Path(os.environ['COUPONGO_M97_PG_SOCKET']).resolve()
    assert socket.name == 'socket' and socket.parent.name.startswith('coupon-pr13-pg.')
    assert socket.parent.stat().st_uid == os.getuid() and socket.parent.stat().st_mode & 0o077 == 0
    assert (socket.parent / 'data' / 'PG_VERSION').is_file()
    assert (socket / '.s.PGSQL.55438').is_socket()
    engine = create_engine(URL.create('postgresql+psycopg2', username=os.environ['USER'],
                                     database='postgres', query={'host':str(socket), 'port':'55438'}))
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)

    def db_override():
        with sessions() as db:
            yield db
    app.dependency_overrides[get_db] = db_override
    try:
        with TestClient(app) as client:
            yield client, sessions
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_postgres_delete_serializes_with_password_reset_and_old_code_cannot_cross_identity(postgres_case, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from app import deps
    from app.routers import auth

    client, sessions = postgres_case
    email = 'pg-race@example.com'
    old, _ = register(client, email)
    with sessions() as db:
        db.add(models.OtpCode(email=email, code='314159', purpose='forgot',
                              expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=10)))
        db.commit()

    started_reset, started_delete = Event(), Event()
    real_lookup, real_identity = auth._lock_email_user, deps._identity
    def mark_lookup(db, value):
        started_reset.set()
        return real_lookup(db, value)
    def mark_identity(*args, **kwargs):
        if kwargs.get('lock_for_write'):
            started_delete.set()
        return real_identity(*args, **kwargs)
    monkeypatch.setattr(auth, '_lock_email_user', mark_lookup)
    monkeypatch.setattr(deps, '_identity', mark_identity)
    gate = sessions()
    try:
        gate.query(models.User).filter_by(id=old['user_id']).with_for_update().one()
        with ThreadPoolExecutor(max_workers=2) as pool:
            reset = pool.submit(client.post, '/api/v3/password/reset',
                                json={'email':email,'code':'314159','new_password':'Changed-987'})
            delete = pool.submit(client.post, '/api/v3/delete-account', headers=token(old), json={})
            try:
                assert started_reset.wait(5) and started_delete.wait(5)
                assert not reset.done() and not delete.done()
            finally:
                gate.rollback()  # Release even when an assertion fails.

            reset_result, delete_result = reset.result(timeout=10), delete.result(timeout=10)
        assert reset_result.status_code in (200, 400)
        assert delete_result.status_code == 200, delete_result.text
    finally:
        gate.close()

    with sessions() as db:
        deleted = db.get(models.User, old['user_id'])
        assert deleted.deleted_at is not None and not deleted.is_active and deleted.email != email
        assert db.query(models.OtpCode).filter_by(email=email).count() == 0
    assert client.get('/api/v3/me', headers=token(old)).status_code == 401
    for password in ('First-Pass-123', 'Changed-987'):
        assert client.post('/api/v3/login', json={'email':email,'password':password}).status_code == 401
    fresh = client.post('/api/v3/new-register', json={'email':email,'password':'Fresh-123','role':'customer'})
    assert fresh.status_code == 200
    assert client.post('/api/v3/password/reset', json={'email':email,'code':'314159','new_password':'Stolen-123'}).status_code != 200


@pytest.mark.parametrize('winner', ['deletion', 'order'])
def test_postgres_delete_serializes_with_new_order(postgres_case, monkeypatch, winner):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from app import deps
    from app.models_packages import Order, Package
    from app.routers import auth, packages

    client, sessions = postgres_case
    user, _ = register(client, 'pg-order-race@example.com')
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with sessions() as db:
        vendor = models.User(email='pg-vendor@example.com', password_hash='!', role='vendor', is_active=True)
        db.add(vendor); db.flush()
        shop = models.Shop(vendor_id=vendor.id, name='PG Test Shop', is_approved=True)
        db.add(shop); db.flush()
        product = Package(shop_id=shop.id, name='PG Test Package', original_price=30, package_price=20,
                          valid_from=now - timedelta(days=1), valid_to=now + timedelta(days=1),
                          stock_total=2, stock_left=2, status='active')
        db.add(product); db.flush()
        pid = product.id
        db.commit()

    lock_held, release, contender_entered = Event(), Event(), Event()
    original_sweep, original_identity = auth.cashback_svc.sweep_user, deps._identity
    gateway_calls = []

    def pause_locked_winner():
        lock_held.set()
        assert release.wait(10)

    def sweep(*args, **kwargs):
        if winner == 'deletion':
            pause_locked_winner()
        return original_sweep(*args, **kwargs)

    def fake_gateway(order_id, amount, method):
        gateway_calls.append(order_id)
        if winner == 'order':
            pause_locked_winner()
        return {'gateway_ref': 'pi_local_pg_fixture', 'client_secret': 'test_fixture', 'currency': 'HKD'}

    def observe_identity(*args, **kwargs):
        if lock_held.is_set() and kwargs.get('lock_for_write'):
            contender_entered.set()
        return original_identity(*args, **kwargs)

    monkeypatch.setattr(auth.cashback_svc, 'sweep_user', sweep)
    monkeypatch.setattr(packages, 'create_payment_intent', fake_gateway)
    monkeypatch.setattr(deps, '_identity', observe_identity)

    def deletion():
        return client.post('/api/v3/delete-account', headers=token(user), json={})

    def order():
        return client.post(f'/api/v3/packages/{pid}/purchase', headers=token(user),
                           json={'payment_method': 'fps', 'client_ref': 'pg-order-fixture'})

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = (deletion, order) if winner == 'deletion' else (order, deletion)
        result1 = pool.submit(first)
        try:
            assert lock_held.wait(10)
            result2 = pool.submit(second)
            assert contender_entered.wait(10)
            assert not result2.done()
        finally:
            release.set()
        r1, r2 = result1.result(timeout=10), result2.result(timeout=10)

    with sessions() as db:
        account = db.get(models.User, user['user_id'])
        orders = db.query(Order).filter_by(user_id=user['user_id']).all()
        if winner == 'deletion':
            assert (r1.status_code, r2.status_code) == (200, 401), (r1.text, r2.text)
            assert account.deleted_at is not None and orders == []
            assert db.get(Package, pid).stock_left == 2 and gateway_calls == []
        else:
            assert (r1.status_code, r2.status_code) == (200, 409), (r1.text, r2.text)
            assert r2.json()['code'] == 'payment_in_flight'
            assert account.deleted_at is None and len(orders) == 1
            assert orders[0].payment_status == 'pending'
            assert db.get(Package, pid).stock_left == 1 and gateway_calls == [orders[0].id]


def test_pending_application_blocks_delete_until_review_is_resolved(case):
    from app.models_line_b import MerchantApplication
    client, sessions = case
    old, _ = register(client, 'merchant-applicant@example.com')
    with sessions() as db:
        application = MerchantApplication(user_id=old['user_id'], shop_name='Pending', status='pending',
                                          contact_name='Applicant', phone='12345678', id_card='sensitive')
        db.add(application); db.commit(); application_id = application.id
    assert client.post('/api/v3/delete-account', headers=token(old), json={}).status_code == 409
    with sessions() as db:
        assert db.get(models.User, old['user_id']).is_active
        db.get(MerchantApplication, application_id).status = 'rejected'
        db.commit()
    assert client.post('/api/v3/delete-account', headers=token(old), json={}).status_code == 200
    with sessions() as db:
        # Application/KYC retention needs explicit policy; the audit row is not silently erased.
        assert db.get(MerchantApplication, application_id) is not None


def test_personal_search_and_favorite_are_removed_and_unused_qr_is_revoked(case):
    client, sessions = case
    old, _ = register(client, 'preferences@example.com')
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with sessions() as db:
        vendor = models.User(email='pref-vendor@example.com', password_hash='!', role='vendor', is_active=True)
        db.add(vendor); db.flush()
        shop = models.Shop(vendor_id=vendor.id, name='Shop', is_approved=True)
        db.add(shop); db.flush()
        coupon = models.Coupon(shop_id=shop.id, vendor_id=vendor.id, coupon_name='Coupon',
                               coupon_type='Gift', valid_from=now,
                               valid_to=now + timedelta(days=1), total=1, remaining=0)
        db.add(coupon); db.flush()
        item = models.WalletItem(user_id=old['user_id'], coupon_id=coupon.id, status=1,
                                 code='123456', qr_expires_at=now + timedelta(minutes=5))
        db.add_all([item, models.SearchHistory(user_id=old['user_id'], search_value='private search'),
                    models.Favorite(user_id=old['user_id'], coupon_id=coupon.id)])
        db.commit(); item_id = item.id
    result = client.post('/api/v3/delete-account', headers=token(old), json={'reason':'private free text'})
    assert result.status_code == 200
    with sessions() as db:
        item = db.get(models.WalletItem, item_id)
        assert item.status == 3 and item.code == '' and item.qr_expires_at is None
        assert db.query(models.SearchHistory).filter_by(user_id=old['user_id']).count() == 0
        assert db.query(models.Favorite).filter_by(user_id=old['user_id']).count() == 0
        note = db.query(models.Notification).filter_by(user_id=old['user_id'], title='帳戶刪除').one()
        assert 'private free text' not in note.body


def test_pending_registration_resend_and_active_password_reset_still_work(case):
    client, sessions = case
    email = 'legitimate@example.com'
    registered = client.post('/api/v3/new-register', json={
        'email': email, 'password': 'First-Pass-123', 'name': 'Original', 'role': 'customer'})
    assert registered.status_code == 200
    assert client.post('/api/v3/new-resent-otp', json={'email':email, 'purpose':'register'}).status_code == 200
    code = client.get('/api/v3/_dev/otp', params={'email':email}).json()['code']
    assert client.post('/api/v3/new-verify-otp', json={'email':email, 'code':code, 'purpose':'register'}).status_code == 200
    assert client.post('/api/v3/password/forgot', json={'email':email}).status_code == 200
    code = client.get('/api/v3/_dev/otp', params={'email':email}).json()['code']
    assert client.post('/api/v3/password/reset', json={'email':email, 'code':code, 'new_password':'New-Pass-456'}).status_code == 200
    assert client.post('/api/v3/login', json={'email':email, 'password':'New-Pass-456'}).status_code == 200
    with sessions() as db:
        assert db.query(models.User).filter_by(email=email).one().is_active


def test_shop_ownership_blocks_deletion_even_if_role_changed(case):
    client, sessions = case
    old, _ = register(client, 'former-vendor@example.com', role='vendor')
    with sessions() as db:
        db.get(models.User, old['user_id']).role = 'customer'
        db.add(models.Shop(vendor_id=old['user_id'], name='Still operating', is_approved=True))
        db.commit()
    assert client.post('/api/v3/delete-account', headers=token(old), json={}).status_code == 409
    with sessions() as db:
        assert db.get(models.User, old['user_id']).deleted_at is None


def test_administrator_cannot_self_delete_before_role_transfer(case):
    client, sessions = case
    old, _ = register(client, 'administrator@example.com')
    with sessions() as db:
        db.get(models.User, old['user_id']).role = 'admin'
        db.commit()
    assert client.post('/api/v3/delete-account', headers=token(old), json={}).status_code == 409
    with sessions() as db:
        assert db.get(models.User, old['user_id']).is_active
        assert db.get(models.User, old['user_id']).deleted_at is None
