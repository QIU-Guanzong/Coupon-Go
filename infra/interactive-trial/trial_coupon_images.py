"""Private, bounded image storage in one synthetic browser's SQLite copy."""
import io
import re
import secrets
import sqlite3
from contextlib import closing

from PIL import Image, ImageOps, UnidentifiedImageError

ROUTE = '/api/v3/trial/coupon-images'
PATH = re.compile(ROUTE + r'/([a-f0-9]{32})\.webp')
MAX_UPLOAD = 5 * 1024 * 1024
MAX_MULTIPART = MAX_UPLOAD + 16384
MAX_PIXELS = 12_000_000
MAX_EDGE = 4096
MAX_STORED_FILE = 1024 * 1024
MAX_STORED_BYTES = 12 * 1024 * 1024
MAX_STORED_FILES = 24
FORMATS = {'image/jpeg': 'JPEG', 'image/png': 'PNG', 'image/webp': 'WEBP'}
SCHEMA = """CREATE TABLE IF NOT EXISTS trial_coupon_images (
    id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL, shop_id INTEGER NOT NULL,
    image BLOB NOT NULL
)"""


class ImageQuota(Exception):
    pass


def sanitize(data, claimed_mime):
    expected = FORMATS.get(claimed_mime)
    if not expected or not data or len(data) > MAX_UPLOAD:
        raise ValueError('unsupported image')
    try:
        with Image.open(io.BytesIO(data), formats=list(FORMATS.values())) as source:
            w, h = source.size
            if (source.format != expected or getattr(source, 'is_animated', False)
                    or max(w, h) > MAX_EDGE or w * h > MAX_PIXELS):
                raise ValueError('unsupported image')
            source.verify()
        with Image.open(io.BytesIO(data), formats=[expected]) as source:
            source.load()
            oriented = ImageOps.exif_transpose(source)
            oriented.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
            converted = oriented.convert('RGBA' if 'A' in oriented.getbands()
                                        or 'transparency' in oriented.info else 'RGB')
            # Fresh pixels only: embedded EXIF/XMP/ICC/text and original bytes are never stored.
            cleaned = Image.frombytes(converted.mode, converted.size, converted.tobytes())
            out = io.BytesIO()
            cleaned.save(out, format='WEBP', quality=82, method=2)
            image = out.getvalue()
        if not image or len(image) > MAX_STORED_FILE:
            raise ValueError('image too large after normalization')
        return image
    except (OSError, SyntaxError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValueError('invalid image') from exc


def _owner(conn, owner_id, shop_id, email):
    return conn.execute(
        'SELECT 1 FROM shops AS s JOIN users AS u ON u.id=s.vendor_id '
        "WHERE s.id=? AND u.id=? AND u.email=? AND u.role='vendor' "
        'AND u.is_active AND u.deleted_at IS NULL',
        (shop_id, owner_id, email),
    ).fetchone() is not None


def can_upload(path, owner_id, shop_id, email):
    with closing(sqlite3.connect(path)) as conn:
        return _owner(conn, owner_id, shop_id, email)


def store(path, owner_id, shop_id, email, image):
    with closing(sqlite3.connect(path, timeout=10)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        if not _owner(conn, owner_id, shop_id, email):
            raise PermissionError()
        conn.execute(SCHEMA)
        count, size = conn.execute('SELECT COUNT(*), COALESCE(SUM(LENGTH(image)),0) '
                                   'FROM trial_coupon_images').fetchone()
        if count >= MAX_STORED_FILES or size + len(image) > MAX_STORED_BYTES:
            raise ImageQuota()
        key = secrets.token_hex(16)
        conn.execute('INSERT INTO trial_coupon_images VALUES(?,?,?,?)',
                     (key, owner_id, shop_id, image))
        return f'{ROUTE}/{key}.webp'


def get(path, key):
    with closing(sqlite3.connect(path)) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='trial_coupon_images' "
                            "AND type='table'").fetchone():
            return None
        row = conn.execute('SELECT image FROM trial_coupon_images WHERE id=?', (key,)).fetchone()
        return row[0] if row else None


def can_attach(path, image, owner_id, email, *, shop_id=None, coupon_id=None):
    if not isinstance(image, str) or not (match := PATH.fullmatch(image)):
        return False
    with closing(sqlite3.connect(path)) as conn:
        if coupon_id is not None:
            found = conn.execute('SELECT shop_id FROM coupons WHERE id=? AND kind!=?',
                                 (coupon_id, 'package')).fetchone()
            if found is None:
                return False
            shop_id = found[0]
        if type(shop_id) is not int or not _owner(conn, owner_id, shop_id, email):
            return False
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='trial_coupon_images' "
                            "AND type='table'").fetchone():
            return False
        return conn.execute(
            'SELECT 1 FROM trial_coupon_images WHERE id=? AND shop_id=? AND owner_id=?',
            (match.group(1), shop_id, owner_id),
        ).fetchone() is not None
