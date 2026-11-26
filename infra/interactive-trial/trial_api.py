"""Dedicated per-browser, synthetic-only interactive trial gateway.

Run as its own single-worker process. Never point at a customer database or
provide Stripe / AI credentials. Demo state is private to one browser and is
intentionally reset on process restart or after inactivity.
"""
import asyncio
from contextlib import closing
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException

import trial_coupon_images as coupon_images
import jwt
import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

_root_env = Path(os.environ['TRIAL_STATE_DIR']).resolve()
if os.environ.get('UPLOAD_DIR') != str(_root_env / 'uploads'):
    raise RuntimeError('trial uploads must stay in the dedicated trial directory')
if os.environ.get('MERCHANT_FILE_DIR') != str(_root_env / 'merchant-files'):
    raise RuntimeError('trial merchant files must stay in the dedicated trial directory')
if os.environ.get('REDIS_URL'):
    raise RuntimeError('trial must not connect to an external Redis service')

from app import config, connect_gateway, connect_service, models, security
from app.database import get_db
from app.main import app as backend
from app.models_line_b import Review
from app.routers import qbot

ROOT = Path(os.environ['TRIAL_STATE_DIR']).resolve()
TEMPLATE = ROOT / 'template.db'
if (os.environ.get('APP_ENV') != 'dev' or os.environ.get('SCHEDULER_ENABLED') != '0'
        or os.environ.get('SEED_ON_STARTUP') != '0'):
    raise RuntimeError('trial requires a dedicated dev simulator and disabled scheduler')
if os.environ.get('DATABASE_URL') != f'sqlite:///{TEMPLATE}':
    raise RuntimeError('trial database must be its isolated template')
if any(os.environ.get(k) for k in ('STRIPE_SECRET_KEY', 'STRIPE_PUBLISHABLE_KEY',
                                    'STRIPE_WEBHOOK_SECRET', 'DEEPSEEK_API_KEY',
                                    'COUPON_WEB_DIR')):
    raise RuntimeError('trial process must not have payment/AI credentials or mount a web root')
if not config.SECRET_KEY or config.SECRET_KEY == config.DEFAULT_SECRET_KEY:
    raise RuntimeError('trial requires its own randomly generated secret')
if not TEMPLATE.is_file() or TEMPLATE.is_symlink():
    raise RuntimeError('fresh synthetic trial template is missing')
marker = ROOT / '.coupon-interactive-trial'
if marker.is_symlink() or not marker.is_file() or marker.read_text() != 'coupon-go-interactive-trial-v1\n':
    raise RuntimeError('trial state directory was not prepared as a synthetic trial')
if config.connect_enabled().get('enabled') is not True:
    raise RuntimeError('trial requires explicitly enabled simulated Connect')

TTL = 4 * 60 * 60
MAX_SESSIONS = int(os.environ.get('TRIAL_MAX_SESSIONS', '200'))
MAX_CREATIONS_PER_MINUTE = int(os.environ.get('TRIAL_CREATIONS_PER_MINUTE', '60'))
COOKIE = 'coupon_trial_session'
COOKIE_PATH = os.environ.get('TRIAL_COOKIE_PATH', '/coupongo-trial/')
if not COOKIE_PATH.startswith('/') or not COOKIE_PATH.endswith('/') or ';' in COOKIE_PATH:
    raise RuntimeError('invalid trial cookie path')
COOKIE_SECURE = os.environ.get('TRIAL_COOKIE_SECURE', '1') == '1'
PUBLIC_ORIGIN = os.environ.get('TRIAL_PUBLIC_ORIGIN', '').rstrip('/')
if not PUBLIC_ORIGIN.startswith(('http://', 'https://')) or config.CORS_ORIGINS != [PUBLIC_ORIGIN]:
    raise RuntimeError('trial requires one explicit same-site origin')
BOOT = secrets.token_hex(16)
SESSIONS_ROOT = ROOT / 'sessions'
if SESSIONS_ROOT.is_symlink():
    raise RuntimeError('trial sessions directory must not be a symlink')
SESSIONS_ROOT.mkdir(exist_ok=True, mode=0o700)
# SQLite copies and the local provider mock are process-scoped. A second worker
# would mix their lifetimes, so fail clearly rather than silently sharing data.
_lock_fd = os.open(ROOT / '.instance.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
try:
    fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError as exc:
    raise RuntimeError('interactive trial requires exactly one worker') from exc

# All old browser sessions are intentionally invalid after restart. Remove
# only files matching our own random session naming scheme under this marked
# directory; unknown files, symlinks and other locations are never removed.
for old in SESSIONS_ROOT.iterdir():
    if old.is_symlink() or not old.is_dir() or not re.fullmatch(r'[0-9a-f]{32}', old.name):
        continue
    for item in old.iterdir():
        if not item.is_symlink() and item.is_file() and re.fullmatch(r'[0-9a-f]{64}\.db(?:-wal|-shm|-journal)?', item.name):
            item.unlink()
    try:
        old.rmdir()
    except OSError:
        pass
SESSIONS = SESSIONS_ROOT / BOOT
SESSIONS.mkdir(parents=True, exist_ok=False, mode=0o700)
LOCK = threading.RLock()
IMAGE_SLOTS = threading.BoundedSemaphore(2)
IN_FLIGHT: dict[str, int] = {}
CREATIONS: list[float] = []
QBOT_CALLS: dict[str, list[float]] = {}
QBOT_URL = os.environ.get('TRIAL_QBOT_PROXY_URL', '')
if QBOT_URL and QBOT_URL != 'http://127.0.0.1:18760/api/v3/qbot/chat':
    raise RuntimeError('trial Q仔 must use the existing local quota gateway')
CURRENT = ContextVar('coupon_trial_session', default=None)
# Exact seed identities, not general name replacements. Keep the template and
# native API untouched; normalize only the private copy in use by this browser.
TRIAL_IDENTITIES = {
    'customer': ('demo@coupon-go.example', 'Demo 用戶', '顧客'),
    'vendor': ('vendor@coupon-go.example', 'Demo 商戶', '商戶'),
}

# The mock provider's idempotency store is process-wide. Namespace its keys
# per isolated browser database, or identical ledger IDs cross-contaminate.
_original_sim_idempotent = connect_gateway._sim_idempotent

def _session_sim_idempotent(key, prefix, seed, build):
    session = CURRENT.get()
    if not session:
        raise RuntimeError('simulated provider cannot run outside a trial session')
    return _original_sim_idempotent(f'{session}:{key}' if key else None,
                                    prefix, f'{session}:{seed}', build)

connect_gateway._sim_idempotent = _session_sim_idempotent


def _session_file(raw):
    if not raw or not re.fullmatch(r'[0-9a-f]{32}\.[A-Za-z0-9_-]{43}', raw):
        return None
    epoch, secret = raw.split('.', 1)
    if epoch != BOOT:
        return None
    sid = hashlib.sha256(secret.encode()).hexdigest()
    path = SESSIONS / f'{sid}.db'
    with LOCK:
        if not path.is_file() or path.is_symlink():
            return None
        if time.time() - path.stat().st_mtime > TTL:
            return None
        # Mark access while holding the cleanup lock so a concurrent new
        # session cannot remove this database between lookup and request use.
        os.utime(path, None)
        return sid, path


def _purge_expired(now):
    # This directory is created exclusively by this process; never follow
    # symlinks or touch any database outside this boot's dedicated directory.
    for path in SESSIONS.glob('*.db'):
        if path.is_symlink() or not re.fullmatch(r'[0-9a-f]{64}\.db', path.name):
            continue
        sid = path.stem
        if not IN_FLIGHT.get(sid) and now - path.stat().st_mtime > TTL:
            path.unlink()
            for suffix in ('-wal', '-shm', '-journal'):
                companion = Path(str(path) + suffix)
                if companion.is_file() and not companion.is_symlink():
                    companion.unlink()
            for cache in (connect_gateway._SIM_IDEMPOTENCY, connect_gateway._SIM_REQUESTS):
                for key in [k for k in cache if isinstance(k, str) and k.startswith(sid + ':')]:
                    del cache[key]
            QBOT_CALLS.pop(sid, None)


def _new_session():
    with LOCK:
        now = time.time()
        _purge_expired(now)
        CREATIONS[:] = [x for x in CREATIONS if now - x < 60]
        if len(CREATIONS) >= MAX_CREATIONS_PER_MINUTE or len(list(SESSIONS.glob('*.db'))) >= MAX_SESSIONS:
            return None
        secret = secrets.token_urlsafe(32)
        sid = hashlib.sha256(secret.encode()).hexdigest()
        path = SESSIONS / f'{sid}.db'
        # Exclusive creation: a request can never choose an arbitrary path.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        try:
            source = sqlite3.connect(f'file:{TEMPLATE}?mode=ro', uri=True)
            dest = sqlite3.connect(path)
            try:
                source.backup(dest)
                for role, (email, old_name, name) in TRIAL_IDENTITIES.items():
                    dest.execute('UPDATE users SET name=? WHERE email=? AND role=? AND name=?',
                                 (name, email, role, old_name))
                created = dest.execute('SELECT created_at FROM trial_template_metadata').fetchone()[0]
                shift = max(0, int(now) - int(created))
                # Preserve each sample offer's original duration when the
                # template has been online longer than its seed validity.
                for table, columns in (('coupons', ('valid_from', 'valid_to')),
                                       ('packages', ('valid_from', 'valid_to')),
                                       ('bargain_campaigns', ('expires_at',))):
                    for column in columns:
                        dest.execute(f'UPDATE {table} SET {column}=datetime({column}, ?) WHERE {column} IS NOT NULL',
                                     (f'+{shift} seconds',))
                dest.commit()
            finally:
                dest.close()
                source.close()
        except Exception:
            path.unlink(missing_ok=True)
            raise
        CREATIONS.append(now)
        return f'{BOOT}.{secret}', sid, path


def _db(path):
    engine = create_engine(f'sqlite:///{path}', connect_args={'check_same_thread': False, 'timeout': 15}, poolclass=NullPool)
    return engine, Session(engine)


def _session_comparison(path, question, comparison):
    engine, db = _db(path)
    try:
        offers = qbot.catalog(db, question.message, comparison)
        return qbot.comparison_answer(offers, question.language, preview=True)
    finally:
        db.close()
        engine.dispose()


def _trial_get_db(request: Request):
    path = request.scope.get('state', {}).get('trial_db_path')
    if path is None:
        raise RuntimeError('missing trial database context')
    engine, db = _db(path)
    try:
        yield db
    finally:
        db.close()
        engine.dispose()

backend.dependency_overrides[get_db] = _trial_get_db

# Exact method/path matches; native API still owns object permissions.
CUSTOMER_ROLE = (("POST", r'/api/v3/(?:reviews|points/exchange|admin/merchant-applications|bargain-campaigns/[0-9]+/start)'),)
VENDOR_ROLE = (
    ("POST", r'/api/v3/(?:add-coupon-preview|coupon-previewed|assign-branches|packages)'),
    ("POST", r'/api/v3/vendor/(?:coupons|packages)/[0-9]+/(?:pause|resume|unlist|submit)'),
    ("PATCH", r'/api/v3/vendor/(?:coupons|packages)/[0-9]+'),
)
ROLE_WRITES = tuple((verb, re.compile(path), role) for role, routes in (
    ('customer', CUSTOMER_ROLE), ('vendor', VENDOR_ROLE)) for verb, path in routes)
WRITE_PATHS = tuple(re.compile(p) for p in (
    r'/api/v3/auto-suggestion-new', r'/api/v3/delete-search-history',
    r'/api/v3/favorite-shop', r'/api/v3/favorite-coupon-all',
    r'/api/v3/new-favorite-coupon', r'/api/v3/store-scaned-qr-values',
    r'/api/v3/new-scan-coupon(?:/prepare|/batch)?',
    r'/api/v3/redemptions/[0-9]+/revoke',
    r'/api/v3/merchant/redemptions/supplementary-scan',
    r'/api/v3/packages/[0-9]+/purchase', r'/api/v3/coupons/[0-9]+/purchase',
    r'/api/v3/orders/[0-9]+/(?:demo-payment|confirm-payment|retry-payment|cancel|refund)',
    r'/api/v3/(?:packages|coupons)/[0-9]+/refund',
    r'/api/v3/notifications/(?:read-all|[0-9]+/read)',
))
DENY_READ = re.compile(r'/api/v3/(?:_dev|dev|merchant-files|uploads|qbot)(?:/|$)')
SETTLE_ACTION = re.compile(r'/api/v3/(?:new-scan-coupon(?:/batch)?|redemptions/[0-9]+/revoke|orders/[0-9]+/refund|packages/[0-9]+/refund|coupons/[0-9]+/refund)')
EXCHANGE_OPTIONS = '/api/v3/points/exchange-options'
MY_REVIEWS = '/api/v3/reviews/mine'
APPLICATION = '/api/v3/admin/merchant-applications'
APPLICATION_NAMES = frozenset({'海港小店', '山邊小館'})
TEXT_REVIEW_KEYS = {'wallet_item_id', 'redemption_id', 'taste', 'ambience', 'service',
                    'content', 'images', 'tags', 'claim_points'}


def _exchange_rows(db, user_id):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    acquired = (db.query(models.WalletItem.id).filter(
        models.WalletItem.coupon_id == models.Coupon.id,
        models.WalletItem.user_id == user_id).exists())
    return (db.query(models.Coupon.id, models.Coupon.coupon_name, models.Shop.name)
            .join(models.Shop, models.Shop.id == models.Coupon.shop_id)
            .filter(models.Coupon.is_points_exchange.is_(True),
                    (models.Coupon.sale_price.is_(None)) | (models.Coupon.sale_price == 0),
                    models.Coupon.status == 'active', models.Coupon.remaining > 0,
                    models.Coupon.valid_from <= now, models.Coupon.valid_to > now,
                    ~acquired))


def _exchange_options(path, user_id):
    engine, db = _db(path)
    try:
        return [{'id': cid, 'coupon_name': name, 'shop_name': shop}
                for cid, name, shop in _exchange_rows(db, user_id)
                .order_by(models.Coupon.id).limit(100)]
    finally:
        db.close()
        engine.dispose()


def _exchange_available(path, user_id, coupon_id):
    engine, db = _db(path)
    try:
        return _exchange_rows(db, user_id).filter(models.Coupon.id == coupon_id).first() is not None
    finally:
        db.close()
        engine.dispose()


def _my_reviews(path, user_id):
    engine, db = _db(path)
    try:
        rows = (db.query(Review, models.Shop.name)
                .join(models.Shop, models.Shop.id == Review.shop_id)
                .filter(Review.user_id == user_id)
                .order_by(Review.created_at.desc(), Review.id.desc()).limit(100))
        return [{'id': review.id, 'shop_name': shop, 'content': review.content or '',
                 'created_at': (review.created_at.isoformat() + 'Z' if review.created_at else None),
                 'overall': review.overall, 'reply_text': review.reply_text or '',
                 'points_given': bool(review.points_given)}
                for review, shop in rows]
    finally:
        db.close()
        engine.dispose()


def _dispatch(path):
    engine, db = _db(path)
    try:
        result = connect_service.sweep_pending_transfers(db)
        if result.get('failed', 0):
            raise RuntimeError('simulated settlement dispatch incomplete')
    finally:
        db.close()
        engine.dispose()


def _image_problem(detail, status):
    return JSONResponse({'detail': detail}, status, headers={'Cache-Control': 'no-store'})


def _vendor_id(header, sid):
    scheme, _, token = header.partition(' ')
    payload = security.decode_token(token) if scheme.lower() == 'bearer' else None
    if not payload or payload.get('trial') != sid or payload.get('role') not in ('customer', 'vendor'):
        return None, 401
    subject = payload.get('sub')
    if (not isinstance(subject, str) or not re.fullmatch(r'[1-9][0-9]{0,17}', subject)
            or payload.get('purpose') is not None):
        return None, 401
    if payload['role'] != 'vendor':
        return None, 403
    return int(subject), 200


async def _upload_coupon_image(scope, receive, sid, path, request):
    owner, error = _vendor_id(request.headers.get('authorization', ''), sid)
    if owner is None:
        return _image_problem('請選擇商家身份', error)
    if (request.headers.get('content-type', '').split(';', 1)[0].strip().lower()
            != 'multipart/form-data' or request.query_params):
        return _image_problem('請選擇一張優惠券圖片', 422)
    try:
        length = int(request.headers.get('content-length', '0'))
        if length < 0:
            raise ValueError()
    except ValueError:
        return _image_problem('內容格式不正確', 400)
    if length > coupon_images.MAX_MULTIPART:
        return _image_problem('圖片過大', 413)
    if not IMAGE_SLOTS.acquire(blocking=False):
        return _image_problem('請稍後再上傳', 429)
    with LOCK:
        IN_FLIGHT[sid] = IN_FLIGHT.get(sid, 0) + 1
    try:
        async def read_image_body():
            data = bytearray()
            while True:
                chunk = await receive()
                if chunk['type'] != 'http.request':
                    raise ConnectionAbortedError()
                next_bytes = chunk.get('body', b'')
                if len(data) + len(next_bytes) > coupon_images.MAX_MULTIPART:
                    raise OverflowError()
                data.extend(next_bytes)
                if not chunk.get('more_body', False):
                    return bytes(data)

        try:
            body = await asyncio.wait_for(read_image_body(), timeout=15)
        except OverflowError:
            return _image_problem('圖片過大', 413)
        except (ConnectionAbortedError, asyncio.TimeoutError):
            return _image_problem('圖片未能上傳', 408)

        async def replay():
            return {'type': 'http.request', 'body': body, 'more_body': False}

        try:
            form_request = Request(scope, replay)
            async with form_request.form(max_files=1, max_fields=1,
                                         max_part_size=256) as form:
                if set(form) != {'files', 'shop_id'}:
                    return _image_problem('請選擇一張優惠券圖片及店舖', 422)
                entry = form['files']
                shop = form['shop_id']
                if (not isinstance(entry, UploadFile) or not isinstance(shop, str)
                        or not re.fullmatch(r'[1-9][0-9]{0,17}', shop)):
                    return _image_problem('請選擇一張優惠券圖片及店舖', 422)
                shop_id = int(shop)
                email = TRIAL_IDENTITIES['vendor'][0]
                if not await asyncio.to_thread(coupon_images.can_upload, path, owner, shop_id, email):
                    return _image_problem('沒有權限', 403)
                raw = await entry.read(coupon_images.MAX_UPLOAD + 1)
                if len(raw) > coupon_images.MAX_UPLOAD:
                    return _image_problem('圖片過大', 413)
                image = await asyncio.to_thread(coupon_images.sanitize, raw, entry.content_type)
                ref = await asyncio.to_thread(coupon_images.store, path, owner, shop_id, email, image)
        except (ValueError, StarletteHTTPException, MultiPartException):
            return _image_problem('圖片無效，請選擇 JPG、PNG 或 WebP', 422)
        except PermissionError:
            return _image_problem('沒有權限', 403)
        except coupon_images.ImageQuota:
            return _image_problem('本次使用的優惠券圖片已達上限', 429)
        return JSONResponse({'images': [ref]}, 201, headers={'Cache-Control': 'no-store'})
    finally:
        with LOCK:
            IN_FLIGHT[sid] -= 1
            if not IN_FLIGHT[sid]:
                del IN_FLIGHT[sid]
        IMAGE_SLOTS.release()


# Only a reviewed fixture from the immutable trial template is shareable.
# Slugs are not session IDs and never resolve against a browser's private DB.
PUBLIC_PACKAGES_PATH = '/api/v3/trial/public-packages/'
PUBLIC_PACKAGE_FIXTURES = {
    'shu-xiang-hotpot-for-four': ('麻辣火鍋四人特價套餐', '蜀香麻辣火鍋城'),
}


def _public_package(slug: str, *, now: datetime | None = None):
    identity = PUBLIC_PACKAGE_FIXTURES.get(slug)
    if identity is None:
        return None
    with closing(sqlite3.connect(f'{TEMPLATE.as_uri()}?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("""
            SELECT p.name, s.name AS shop_name, p.shop_id, p.package_price,
                   p.original_price, p.person_count, p.valid_from, p.valid_to,
                   p.status, p.stock_left, p.refund_policy, p.items, p.usage_rules
            FROM packages p JOIN shops s ON p.shop_id=s.id
            WHERE p.name=? AND s.name=? AND s.is_approved=1 LIMIT 2
        """, identity).fetchall()
        if len(rows) != 1 or rows[0]['status'] not in ('active', 'sold_out', 'expired'):
            return None
        row = rows[0]
        branches = db.execute(
            'SELECT name AS branch_name, address FROM branches WHERE shop_id=? ORDER BY id',
            (row['shop_id'],),
        ).fetchall()
    try:
        def utc(value):
            dt = datetime.fromisoformat(value)
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)

        start, end = utc(row['valid_from']), utc(row['valid_to'])
        now = now or datetime.now(timezone.utc)
        price, original = float(row['package_price']), float(row['original_price'])
        raw_items, raw_rules = json.loads(row['items']), json.loads(row['usage_rules'])
        if (start > now or end <= start or not math.isfinite(original) or
                not 0 < price <= original or type(row['stock_left']) is not int or
                type(row['person_count']) is not int or row['person_count'] <= 0 or
                row['refund_policy'] not in ('anytime', 'expiry_only', 'non_refundable') or
                not isinstance(raw_items, list) or not isinstance(raw_rules, dict)):
            return None
        if not raw_items or any(
            not isinstance(item, dict) or not isinstance(item.get('name'), str) or
            type(item.get('qty')) is not int or item['qty'] <= 0 for item in raw_items
        ):
            return None
    except (TypeError, ValueError, OverflowError):
        return None
    # Explicit field projection: no generic package serializer, database IDs,
    # contact data, order counts, customer reviews or account-derived values.
    rules = {key: raw_rules[key] for key in ('need_booking', 'stackable', 'weekday_only', 'holiday')
             if type(raw_rules.get(key)) is bool}
    for key in ('time_slots', 'includes', 'excludes'):
        value = raw_rules.get(key)
        if isinstance(value, list) and all(isinstance(entry, str) for entry in value):
            rules[key] = value
    status = ('expired' if row['status'] == 'expired' or end <= now else
              'sold_out' if row['status'] == 'sold_out' or row['stock_left'] <= 0 else 'active')
    return {
        'slug': slug,
        'package': {
            'name': row['name'], 'shop_name': row['shop_name'],
            'package_price': price, 'original_price': original,
            'person_count': row['person_count'], 'status': status,
            'valid_from': start.isoformat(), 'valid_to': end.isoformat(),
            'refund_policy': row['refund_policy'], 'usage_rules': rules,
            'items': [{'name': item['name'], 'qty': item['qty']} for item in raw_items],
            'branches': [dict(branch) for branch in branches],
        },
    }


class TrialGateway:
    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            if scope['type'] == 'websocket':
                return await send({'type': 'websocket.close', 'code': 1008})
            return await backend(scope, receive, send)
        request = Request(scope, receive)
        path, method = scope['path'], scope['method'].upper()
        origin = request.headers.get('origin')
        if origin and origin.rstrip('/') != PUBLIC_ORIGIN:
            return await JSONResponse({'detail': '來源不符'}, 403)(scope, receive, send)
        if path.startswith(PUBLIC_PACKAGES_PATH):
            headers = {'Cache-Control': 'no-store'}
            if method != 'GET':
                return await JSONResponse({'detail': '此操作不可用'}, 405, headers={
                    **headers, 'Allow': 'GET',
                })(scope, receive, send)
            slug = path[len(PUBLIC_PACKAGES_PATH):]
            if slug not in PUBLIC_PACKAGE_FIXTURES or request.query_params:
                return await JSONResponse({'detail': '此連結已失效'}, 404, headers=headers)(scope, receive, send)
            try:
                package = await asyncio.to_thread(_public_package, slug)
            except sqlite3.Error:
                return await JSONResponse({'detail': '暫時無法顯示套餐'}, 503, headers=headers)(scope, receive, send)
            if package is None:
                return await JSONResponse({'detail': '此連結已失效'}, 404, headers=headers)(scope, receive, send)
            return await JSONResponse(package, headers=headers)(scope, receive, send)
        if path == coupon_images.ROUTE or coupon_images.PATH.fullmatch(path):
            found_image_session = _session_file(request.cookies.get(COOKIE))
            if method == 'POST' and path == coupon_images.ROUTE:
                if not found_image_session:
                    return await _image_problem('連線已逾時，請重新載入', 401)(scope, receive, send)
                image_reply = await _upload_coupon_image(scope, receive, *found_image_session, request)
                return await image_reply(scope, receive, send)
            if method == 'GET' and (image_match := coupon_images.PATH.fullmatch(path)):
                if found_image_session and not request.query_params:
                    image = await asyncio.to_thread(coupon_images.get, found_image_session[1],
                                                    image_match.group(1))
                    if image is not None:
                        return await Response(image, media_type='image/webp', headers={
                            'Cache-Control': 'private, no-store',
                            'Cross-Origin-Resource-Policy': 'same-origin',
                            'X-Content-Type-Options': 'nosniff',
                        })(scope, receive, send)
                return await _image_problem('找不到圖片', 404)(scope, receive, send)
            return await _image_problem('此操作不可用', 403)(scope, receive, send)
        if method in ('POST', 'PATCH'):
            # Read a small bounded JSON body, including chunked requests.
            body = bytearray()
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect':
                    return
                body.extend(message.get('body', b''))
                if len(body) > 16384:
                    return await JSONResponse({'detail': '內容過長'}, 413)(scope, receive, send)
                if not message.get('more_body', False):
                    break
            sent = False
            original_receive = receive
            async def replay():
                nonlocal sent
                if not sent:
                    sent = True
                    return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
                return await original_receive()
            receive = replay
            request = Request(scope, receive)
        found = _session_file(request.cookies.get(COOKIE))
        if path == '/api/v3/trial/session' and method == 'POST':
            reset = found is None
            if reset:
                created = await asyncio.to_thread(_new_session)
                if created is None:
                    return await JSONResponse({'detail': '目前使用人數較多，請稍後再試'}, 503)(scope, receive, send)
                raw, sid, session_path = created
            else:
                sid, session_path = found
                raw = request.cookies[COOKIE]
                os.utime(session_path, None)
            response = JSONResponse({'mode': 'simulated', 'reset': reset,
                'expires_after_minutes': TTL // 60})
            response.set_cookie(COOKIE, raw, max_age=TTL, path=COOKIE_PATH,
                                httponly=True, secure=COOKIE_SECURE, samesite='strict')
            return await response(scope, receive, send)
        if not found:
            return await JSONResponse({'detail': '連線已逾時，請重新載入'}, 401)(scope, receive, send)
        sid, session_path = found
        if path == '/api/v3/qbot/chat' and method == 'POST':
            if not QBOT_URL:
                return await JSONResponse({'detail': 'Q仔暫未開放，請稍後再試'}, 503)(scope, receive, send)
            with LOCK:
                calls = [t for t in QBOT_CALLS.get(sid, []) if time.time() - t < 60]
                limited = len(calls) >= 5
                if not limited:
                    calls.append(time.time())
                    QBOT_CALLS[sid] = calls
            if limited:
                return await JSONResponse({'detail': 'Q仔忙碌中，請稍後再試'}, 429)(scope, receive, send)
            try:
                async with httpx.AsyncClient(timeout=25, trust_env=False) as client:
                    result = await client.post(QBOT_URL, content=bytes(body),
                                               headers={'Content-Type': 'application/json'})
                data = result.json()
                # Only after the existing shared gateway admitted this request.
                # The browser's own demo shop may have changed price or stock.
                if result.status_code == 200:
                    question = qbot.Question.model_validate_json(bytes(body))
                    comparison = qbot.comparison_request(question.message)
                    if comparison:
                        data = await asyncio.to_thread(_session_comparison, session_path, question, comparison)
                return await JSONResponse(data, result.status_code,
                                          headers={'Cache-Control': 'no-store'})(scope, receive, send)
            except (httpx.HTTPError, ValueError):
                return await JSONResponse({'detail': 'Q仔暫時未能連線，請稍後再試'}, 503)(scope, receive, send)
        if path == '/api/v3/trial/role' and method == 'POST':
            os.utime(session_path, None)
            try:
                payload = await request.json()
                role = payload.get('role') if isinstance(payload, dict) else None
                if not isinstance(role, str):
                    role = None
            except (ValueError, TypeError):
                role = None
            identity = TRIAL_IDENTITIES.get(role)
            if identity is None:
                return await JSONResponse({'detail': '請選擇身份'}, 400)(scope, receive, send)
            email, old_name, name = identity
            def get_user():
                engine, db = _db(session_path)
                try:
                    user = db.query(models.User).filter_by(email=email, role=role, is_active=True).first()
                    if user and user.name == old_name:
                        user.name = name
                        db.commit()
                    return user.id if user else None
                finally:
                    db.close()
                    engine.dispose()
            user_id = await asyncio.to_thread(get_user)
            if user_id is None:
                return await JSONResponse({'detail': '目前無法切換身份'}, 503)(scope, receive, send)
            claims = {'sub': str(user_id), 'role': role, 'trial': sid,
                      'exp': datetime.now(timezone.utc) + timedelta(seconds=TTL)}
            token = jwt.encode(claims, config.SECRET_KEY, algorithm=config.ALGORITHM)
            return await JSONResponse({'access_token': token, 'role': role, 'mode': 'simulated'})(scope, receive, send)
        legacy_allowed = method == 'POST' and any(p.fullmatch(path) for p in WRITE_PATHS)
        required_role = next((role for verb, pattern, role in ROLE_WRITES
                              if method == verb and pattern.fullmatch(path)), None)
        is_exchange_list = path == EXCHANGE_OPTIONS and method == 'GET'
        is_my_reviews = path == MY_REVIEWS and method == 'GET'
        if (not path.startswith('/api/v3/') or DENY_READ.match(path)
                or (path.startswith('/api/v3/admin') and not (path == APPLICATION and method == 'POST'))
                or (path == '/api/v3/merchant/connect/payouts' and method not in ('GET', 'HEAD'))):
            return await JSONResponse({'detail': '此功能不可用'}, 403)(scope, receive, send)
        if method not in ('GET', 'HEAD') and not (legacy_allowed or required_role):
            return await JSONResponse({'detail': '此操作不可用'}, 403)(scope, receive, send)
        auth = request.headers.get('authorization', '')
        payload = None
        if auth:
            scheme, _, token = auth.partition(' ')
            payload = security.decode_token(token) if scheme.lower() == 'bearer' else None
            if not payload or payload.get('trial') != sid or payload.get('role') not in ('customer', 'vendor'):
                return await JSONResponse({'detail': '身份已失效，請重新選擇'}, 401)(scope, receive, send)
        if required_role or is_exchange_list or is_my_reviews:
            if payload is None:
                return await JSONResponse({'detail': '請選擇身份'}, 401)(scope, receive, send)
            if payload.get('role') != (required_role or 'customer'):
                return await JSONResponse({'detail': '目前身份無權執行此操作'}, 403)(scope, receive, send)
        if is_my_reviews:
            rows = await asyncio.to_thread(_my_reviews, session_path, int(payload['sub']))
            return await JSONResponse({'reviews': rows}, headers={'Cache-Control': 'no-store'})(scope, receive, send)
        if is_exchange_list:
            coupons = await asyncio.to_thread(_exchange_options, session_path, int(payload['sub']))
            return await JSONResponse({'cost': config.POINTS_EXCHANGE_COST, 'coupons': coupons},
                                      headers={'Cache-Control': 'no-store'})(scope, receive, send)
        if method == 'POST' and path == '/api/v3/points/exchange':
            coupons = request.query_params.getlist('coupon_id')
            if len(coupons) != 1 or not re.fullmatch(r'[0-9]{1,18}', coupons[0]):
                return await JSONResponse({'detail': '請選擇優惠券'}, 422)(scope, receive, send)
            if not await asyncio.to_thread(_exchange_available, session_path, int(payload['sub']), int(coupons[0])):
                return await JSONResponse({'detail': '此優惠券現時不可兌換'}, 400)(scope, receive, send)
        if (method == 'POST' and path in (APPLICATION, '/api/v3/reviews', '/api/v3/add-coupon-preview',
                                         '/api/v3/coupon-previewed')
                or method == 'PATCH' and re.fullmatch(r'/api/v3/vendor/coupons/[0-9]+', path)):
            try:
                form = json.loads(body)
            except (UnicodeDecodeError, ValueError):
                form = None
            valid = isinstance(form, dict)
            if valid and path == APPLICATION:
                name = form.get('shop_name')
                valid = (set(form) == {'shop_name'} and isinstance(name, str) and name in APPLICATION_NAMES)
            elif valid and path == '/api/v3/reviews':
                valid = set(form) <= TEXT_REVIEW_KEYS and ('images' not in form or form['images'] == [])
            elif valid and form.get('image') not in ('', None):
                owner, _ = _vendor_id(auth, sid)
                coupon = re.fullmatch(r'/api/v3/vendor/coupons/([1-9][0-9]{0,17})', path)
                valid = owner is not None and await asyncio.to_thread(
                    coupon_images.can_attach, session_path, form['image'], owner,
                    TRIAL_IDENTITIES['vendor'][0], shop_id=form.get('shop_id'),
                    coupon_id=int(coupon.group(1)) if coupon else None,
                )
                if method == 'PATCH' and not coupon:
                    valid = False
            if not valid:
                return await JSONResponse({'detail': '請只提交此頁提供的資料'}, 422)(scope, receive, send)
        if ((method == 'POST' and path == '/api/v3/packages')
                or (method == 'PATCH' and re.fullmatch(r'/api/v3/vendor/packages/[0-9]+', path))):
            try:
                package = json.loads(body)
            except (UnicodeDecodeError, ValueError):
                package = None  # Native validation owns other malformed package fields.
            media = package.get('images') if isinstance(package, dict) else None
            if isinstance(media, list) and any(
                isinstance(image, str) and coupon_images.ROUTE + '/' in image for image in media
            ):
                return await _image_problem('優惠券圖片只供優惠券使用', 422)(scope, receive, send)
        try:
            length = int(request.headers.get('content-length', '0') or 0)
        except ValueError:
            return await JSONResponse({'detail': '內容格式不正確'}, 400)(scope, receive, send)
        if length > 16384:
            return await JSONResponse({'detail': '內容過長'}, 413)(scope, receive, send)
        with LOCK:
            IN_FLIGHT[sid] = IN_FLIGHT.get(sid, 0) + 1
            os.utime(session_path, None)
        scoped = dict(scope)
        scoped['state'] = dict(scope.get('state', {}), trial_db_path=session_path)
        context = CURRENT.set(sid)
        messages = []
        status = 500
        async def capture(message):
            nonlocal status
            if message['type'] == 'http.response.start':
                status = message['status']
            messages.append(message)
        try:
            await backend(scoped, receive, capture)
            if method == 'POST' and SETTLE_ACTION.fullmatch(path) and 200 <= status < 300:
                try:
                    await asyncio.to_thread(_dispatch, session_path)
                except Exception:
                    # The API response remains authoritative for the action;
                    # pending mock transfers are visibly pending, not paid.
                    pass
            for message in messages:
                await send(message)
        finally:
            CURRENT.reset(context)
            with LOCK:
                IN_FLIGHT[sid] -= 1
                if not IN_FLIGHT[sid]:
                    del IN_FLIGHT[sid]

app = TrialGateway()
