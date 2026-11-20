"""Versioned, explicit acceptance of externally published legal documents."""
import os
import re
from urllib.parse import urlsplit

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import config, models

VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


def documents():
    """No built-in or sample documents; production must be configured."""
    values = {kind: {
        "version": os.getenv(f"LEGAL_{kind.upper()}_VERSION", "").strip(),
        "url": os.getenv(f"LEGAL_{kind.upper()}_URL", "").strip(),
    } for kind in ("terms", "privacy")}
    if config.APP_ENV == "dev" and not any(v for doc in values.values() for v in doc.values()):
        return {}
    for doc in values.values():
        try:
            parsed = urlsplit(doc["url"])
            valid = (parsed.scheme == "https" and parsed.hostname and
                     not parsed.username and not parsed.password and
                     not parsed.hostname.endswith((".example", ".invalid", ".localhost")) and
                     (config.APP_ENV == "dev" or
                      not parsed.hostname.endswith((".test", ".example.com", ".example.net", ".example.org")) and
                      parsed.hostname not in ("example.com", "example.net", "example.org", "localhost")))
        except ValueError:
            valid = False
        if not valid or not VERSION.fullmatch(doc["version"]) or len(doc["url"]) > 1024:
            raise HTTPException(503, detail={"code": "legal_documents_unavailable"})
    return values


def _record(db: Session, user_id: int, docs):
    return db.scalar(select(models.LegalAcceptance).where(
        models.LegalAcceptance.user_id == user_id,
        models.LegalAcceptance.terms_version == docs["terms"]["version"],
        models.LegalAcceptance.privacy_version == docs["privacy"]["version"],
    ))


def has_accepted(db: Session, user_id: int, docs):
    if not docs:
        return True
    row = _record(db, user_id, docs)
    return bool(row is not None and row.terms_url == docs["terms"]["url"]
                and row.privacy_url == docs["privacy"]["url"])


def require_versions(body, docs, code=400):
    if docs and (body.accepted_terms_version != docs["terms"]["version"] or
                 body.accepted_privacy_version != docs["privacy"]["version"]):
        raise HTTPException(code, detail={"code": "legal_versions_changed", "documents": docs})


def accept(db: Session, user_id: int, body, docs, *, mismatch_status=400):
    if not docs:
        return
    require_versions(body, docs, mismatch_status)
    if has_accepted(db, user_id, docs):
        return
    try:
        with db.begin_nested():
            db.add(models.LegalAcceptance(
                user_id=user_id, terms_version=docs["terms"]["version"],
                terms_url=docs["terms"]["url"], privacy_version=docs["privacy"]["version"],
                privacy_url=docs["privacy"]["url"],
            ))
            db.flush()
    except IntegrityError:
        # Simultaneous acceptance of the same immutable pair is idempotent.
        if not has_accepted(db, user_id, docs):
            raise HTTPException(503, detail={"code": "legal_document_version_conflict"})


def status(db: Session, user_id: int, docs):
    return {"consent_required": not has_accepted(db, user_id, docs), "legal_documents": docs}
