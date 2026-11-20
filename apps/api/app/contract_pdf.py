"""Signed-contract PDF archive (P1-D).

Simple on purpose: a one-page-ish A4 record of what was signed -- title,
version, merchant/applicant, timestamps, IP, and the contract body --
plus a SHA-256 of the body text so the archive can be verified against
the stored contract version. Chinese is rendered with Noto Sans CJK TC
when the font is present; without it we fall back to Helvetica and
render only the ASCII-safe fields (still a valid, downloadable PDF --
see docs/P1D_API.md section 4 for the pre-launch font note).
"""
import hashlib
import io
import logging
import os
from datetime import datetime

from . import config

logger = logging.getLogger(__name__)

# (path, TTC subfont index) candidates, in order. WQY Zen Hei is
# TrueType-outlined, which reportlab handles; the Noto CJK TTCs on
# this box are CFF-outlined, which reportlab rejects (TTFError) and
# are skipped gracefully. Production must ship one of these fonts
# (e.g. apt install fonts-wqy-zenhei) — see docs/P1D_API.md §4.
_FONT_CANDIDATES = [
    ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 0),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 3),
    ("/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc", 3),
]
# Noto CJK TTC subfont order: 0 JP, 1 KR, 2 SC, 3 TC, 4 HK.


def _try_register(name, path, subfont_index):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    if not path or not os.path.exists(path):
        return False
    try:
        pdfmetrics.registerFont(TTFont(name, path,
                                       subfontIndex=subfont_index))
        return True
    except Exception:
        logger.warning("contract PDF: cannot use CJK font %s", path,
                       exc_info=True)
        return False


def _register_fonts():
    """Return (regular, bold) reportlab font names, or (None, None)."""
    try:
        from reportlab.pdfbase import pdfmetrics  # noqa: F401
        from reportlab.pdfbase.ttfonts import TTFont  # noqa: F401
    except ImportError:  # pragma: no cover - reportlab is a hard dep
        return None, None
    candidates = list(_FONT_CANDIDATES)
    if config.MERCHANT_CONTRACT_FONT:
        # An operator-supplied font: try it as a plain TTF first, then
        # as a TTC's Traditional-Chinese subfont.
        candidates.insert(0, (config.MERCHANT_CONTRACT_FONT, 0))
        candidates.insert(1, (config.MERCHANT_CONTRACT_FONT, 3))
    regular = None
    for path, index in candidates:
        if _try_register("P1D-CJK", path, index):
            regular = "P1D-CJK"
            break
    # WQY Zen Hei ships a single weight; headings use the same font.
    # (A bold CJK TTC would be CFF-outlined here and reportlab rejects
    # it, so no bold candidate is attempted.)
    return regular, regular


def render_contract_pdf(*, version, title, body_text, shop_name,
                        applicant_name, applicant_email, user_id,
                        application_id, signed_at, ip_address):
    """Build the signed-contract PDF; returns bytes starting with %PDF."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    regular, bold = _register_fonts()
    cjk = regular is not None
    if not cjk:
        logger.warning(
            "contract PDF: no CJK font available, rendering ASCII-only "
            "fallback for application %s", application_id)
    base_font = regular or "Helvetica"
    head_font = bold or base_font
    body_hash = hashlib.sha256(body_text.encode("utf-8")).hexdigest()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
        title="商户合作协议 v%s -- 签署存档" % version,
        author="Coupon GO!")
    title_style = ParagraphStyle("title", fontName=head_font, fontSize=16,
                                 leading=22, spaceAfter=6 * mm)
    meta_style = ParagraphStyle("meta", fontName=base_font, fontSize=11,
                                leading=16, spaceAfter=2 * mm)
    body_style = ParagraphStyle("body", fontName=base_font, fontSize=11,
                                leading=17, spaceAfter=3 * mm)

    def esc(text):
        return (str(text).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;"))

    def para(text, style=body_style):
        # reportlab Paragraph needs <br/> for line breaks, not \n.
        return Paragraph(esc(text).replace("\n", "<br/>"), style)

    signed_str = (signed_at.isoformat() if isinstance(signed_at, datetime)
                  else str(signed_at))
    story = [Paragraph(esc(title), title_style),
             para("合約版本：v%s" % version, meta_style),
             para("商戶：%s" % shop_name, meta_style),
             para("申請人：%s（%s）" % (applicant_name, applicant_email),
                  meta_style),
             para("申請編號：%s　用戶編號：%s" % (application_id, user_id),
                  meta_style),
             para("簽署時間：%s（UTC）" % signed_str, meta_style),
             para("簽署 IP：%s" % ip_address, meta_style),
             para("合約正文 SHA-256：%s" % body_hash, meta_style),
             Spacer(1, 4 * mm)]
    if cjk:
        for chunk in body_text.split("\n"):
            if chunk.strip():
                story.append(para(chunk))
            else:
                story.append(Spacer(1, 2 * mm))
    else:
        story.append(para(
            "【本存檔以 ASCII 字段為準；合約正文以系統存儲版本為準，"
            "SHA-256 見上：%s】" % body_hash))
    doc.build(story)
    return buf.getvalue()
