"""Trial API and same-origin asset round trips using only synthetic data."""
from datetime import datetime, timedelta, timezone
from io import BytesIO
import os

from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin

from test_trial_api import gateway, role, trial_browser


ROOT = '/api/v3/trial/coupon-images'


def photo(fmt='PNG', size=(42, 24)):
    out = BytesIO()
    args = {}
    if fmt == 'PNG':
        info = PngImagePlugin.PngInfo()
        info.add_text('Description', 'fixture metadata must not be carried')
        args['pnginfo'] = info
    Image.new('RGB', size, '#24a471').save(out, format=fmt, **args)
    return out.getvalue()


def upload(client, headers=None, *, shop='1', data=None, mime='image/png'):
    return client.post(ROOT, headers=headers, data={'shop_id': shop},
                       files={'files': ('cover.png', photo() if data is None else data, mime)})


def coupon(**fields):
    now = datetime.now(timezone.utc)
    return {'shop_id': 1, 'coupon_type': 'Discount', 'coupon_name': '試券封面測試',
            'percentage': 80, 'total': 8, 'valid_from': (now - timedelta(hours=1)).isoformat(),
            'valid_to': (now + timedelta(days=2)).isoformat(), **fields}


def test_shop_owner_upload_preview_create_edit_and_cookie_read(gateway, monkeypatch):
    # Mirror the proxy: path prefix stripped, browser cookie scope preserved.
    monkeypatch.setattr(gateway, 'COOKIE_PATH', '/coupongo-trial/')
    prefix = '/coupongo-trial'

    async def proxy(scope, receive, send):
        if scope['type'] == 'http' and scope['path'].startswith(prefix + '/'):
            scope = dict(scope, path=scope['path'][len(prefix):])
        await gateway.app(scope, receive, send)

    a = TestClient(proxy)
    assert a.post(prefix + '/api/v3/trial/session').status_code == 200
    v = a.post(prefix + '/api/v3/trial/role', json={'role': 'vendor'})
    headers = {'Authorization': 'Bearer ' + v.json()['access_token']}
    original = photo(size=(1600, 600))
    r = a.post(prefix + ROOT, headers=headers, data={'shop_id': '1'},
               files={'files': ('first.png', original, 'image/png')})
    assert r.status_code == 201, r.text
    path = r.json()['images'][0]
    cover = a.get(prefix + path)  # Image.network sends cookie, not bearer.
    assert cover.status_code == 200 and cover.headers['content-type'] == 'image/webp'
    assert cover.headers['cache-control'] == 'private, no-store'
    assert cover.headers['cross-origin-resource-policy'] == 'same-origin'
    assert cover.headers['x-content-type-options'] == 'nosniff'
    with Image.open(BytesIO(cover.content)) as img:
        assert img.size == (1280, 480)
        assert 'exif' not in img.info and 'icc_profile' not in img.info
    assert cover.content != original
    for route in ('/add-coupon-preview', '/coupon-previewed'):
        result = a.post(prefix + '/api/v3' + route, headers=headers, json=coupon(image=path))
        assert result.status_code == 200, result.text
    cid = result.json()['coupon']['id']
    assert result.json()['coupon']['image'] == path
    replacement = a.post(prefix + ROOT, headers=headers, data={'shop_id': '1'},
                         files={'files': ('second.jpg', photo('JPEG'), 'image/jpeg')})
    assert replacement.status_code == 201, replacement.text
    image2 = replacement.json()['images'][0]
    patch = a.patch(prefix + f'/api/v3/vendor/coupons/{cid}', headers=headers,
                    json={'image': image2})
    assert patch.status_code == 200, patch.text
    assert a.get(prefix + f'/api/v3/vendor/coupons/{cid}', headers=headers).json()['coupon']['image'] == image2


def test_only_current_seed_vendor_and_owned_shop_can_upload(gateway):
    a, b, guest = trial_browser(gateway), trial_browser(gateway), TestClient(gateway.app)
    v, c, bv = role(a, 'vendor'), role(a, 'customer'), role(b, 'vendor')
    assert upload(guest, headers=v).status_code == 401
    assert upload(a).status_code == 401
    assert upload(a, c).status_code == 403
    assert upload(b, v).status_code == 401
    assert upload(a, v, shop='9999').status_code == 403
    path = upload(a, v).json()['images'][0]
    assert guest.get(path).status_code == 404
    assert b.get(path, headers=bv).status_code == 404
    assert b.post('/api/v3/add-coupon-preview', headers=bv,
                  json=coupon(image=path)).status_code == 422
    assert a.post('/api/v3/add-coupon-preview', headers=v,
                  json=coupon(shop_id=9999, image=path)).status_code == 422
    assert a.post('/api/v3/packages', headers=v, json={'images': [path]}).status_code == 422
    assert a.patch('/api/v3/vendor/packages/1', headers=v,
                   json={'images': [path]}).status_code == 422
    assert a.post('/api/v3/uploads/review-images', headers=c,
                  files={'files': ('p.png', photo(), 'image/png')}).status_code == 403
    assert a.post('/api/v3/merchant-files', headers=v, content=b'example').status_code == 403
    assert a.get('/api/v3/uploads/merchant-images/old.webp').status_code == 403


def test_rejects_input_without_storing_then_honors_quota(gateway, monkeypatch):
    a = trial_browser(gateway)
    v = role(a, 'vendor')
    assert upload(a, v, data=b'not an image').status_code == 422
    assert upload(a, v, data=photo(), mime='image/jpeg').status_code == 422
    assert upload(a, v, data=photo(size=(4097, 1))).status_code == 422
    assert upload(a, v, data=b'x' * (5 * 1024 * 1024 + 1)).status_code == 413
    assert a.post(ROOT, headers=v, data={'shop_id': '1'}, files=[
        ('files', ('one.png', photo(), 'image/png')),
        ('files', ('two.png', photo(), 'image/png'))]).status_code == 422
    # httpx streams this request without a Content-Length.
    streamed_headers = dict(v, **{'Content-Type': 'multipart/form-data; boundary=couponfixture'})
    stream = iter([b'x' * 262144] * 21)
    assert a.post(ROOT, headers=streamed_headers, content=stream).status_code == 413
    monkeypatch.setattr(gateway.coupon_images, 'MAX_STORED_FILES', 1)
    assert upload(a, v, data=photo('WEBP'), mime='image/webp').status_code == 201
    assert upload(a, v).status_code == 429


def test_image_dies_with_browser_session(gateway, monkeypatch):
    a = trial_browser(gateway)
    path = upload(a, role(a, 'vendor')).json()['images'][0]
    state = gateway._session_file(a.cookies.get(gateway.COOKIE))[1]
    os.utime(state, (10, 10))
    assert a.get(path).status_code == 404
    assert a.post('/api/v3/trial/session').json()['reset'] is True
    assert not state.exists()
    assert a.get(path).status_code == 404
