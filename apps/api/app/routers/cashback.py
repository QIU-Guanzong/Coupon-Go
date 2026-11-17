"""Line D — cashback routes (返现 2B).

Paths follow FEATURE_RESEARCH_20261116.md §12 (返现 section):
  GET  /cashback                  返现余额 + 明细（按状态分组）
  GET  /cashback/transactions     返现流水（含预计确认时间）
  POST /cashback/withdraw         申请提现（FPS / 银行）
  GET  /cashback/withdrawals      提现记录
  POST /cashback/missing          Missing Cashback 申诉

FIX-2: POST /cashback/record (user-supplied amount / merchant / rate)
was DELETED. Cashback is now credited only server-side, inside the
successful vendor scan (/new-scan-coupon), from the redemption record
(cashback_service.record_spend). No client-supplied
spend can ever create a cashback earning.

Plus one D-line endpoint the report's chain needs but does not name:
  POST /cashback/transactions/{id}/void   取消 / 退货作废

All amounts are HK$. Cashback is a separate ledger from points.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import config, models
from ..database import get_db
from ..deps import get_current_user
from .. import cashback_service as svc
from ..cashback_service import CashbackError
from ..models_cashback import CashbackTransaction, CashbackWithdrawal

router = APIRouter()


class WithdrawIn(BaseModel):
    amount: float = Field(gt=0)
    method: str = "fps"                  # fps | bank
    account_name: str
    account_ref: str = ""                # FPS ID / 银行账号


class MissingIn(BaseModel):
    merchant_id: int
    order_no: str
    spent_at: datetime
    amount: float = Field(gt=0)
    evidence: str = ""                   # 订单确认截图链接 / 描述
    note: str = ""


class VoidIn(BaseModel):
    reason: str = "cancelled"            # cancelled | returned


def _handle(fn):
    try:
        return fn()
    except CashbackError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/cashback")
def get_cashback(user: models.User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    svc.sweep_user(db, user.id)
    db.commit()
    txns = (db.query(CashbackTransaction)
            .filter(CashbackTransaction.user_id == user.id)
            .order_by(CashbackTransaction.id.desc()).limit(50).all())
    groups: dict[str, list] = {}
    for t in txns:
        groups.setdefault(t.status, []).append(svc._tx_out(db, t))
    return {**svc.balances(db, user.id),
            # Gap 4: withdraw limits, from config (not hard-coded).
            "min_withdraw_hkd": config.CASHBACK_MIN_WITHDRAW_HKD,
            "daily_withdraw_limit_hkd": config.CASHBACK_DAILY_WITHDRAW_LIMIT_HKD,
            "transactions": [svc._tx_out(db, t) for t in txns],
            "adjustments": svc.adjustments(db, user.id),
            "by_status": {k: len(v) for k, v in groups.items()}}


@router.get("/cashback/transactions")
def list_transactions(status: str = Query(None),
                      limit: int = Query(50, ge=1, le=200),
                      user: models.User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    svc.sweep_user(db, user.id)
    db.commit()
    q = db.query(CashbackTransaction).filter(
        CashbackTransaction.user_id == user.id)
    if status:
        q = q.filter(CashbackTransaction.status == status)
    txns = q.order_by(CashbackTransaction.id.desc()).limit(limit).all()
    return {"transactions": [svc._tx_out(db, t) for t in txns]}


@router.post("/cashback/withdraw")
def withdraw(body: WithdrawIn, user: models.User = Depends(get_current_user),
             db: Session = Depends(get_db)):
    def _do():
        w = svc.withdraw(db, user, body.amount, body.method,
                         body.account_name, body.account_ref)
        db.commit()
        return {"message": "提現申請已提交，待打款",
                "withdrawal": svc._withdrawal_out(w),
                **svc.balances(db, user.id)}
    # Gap 6: a failed withdrawal carries the server-side reason in the
    # response body (failure_reason), not just a generic error message.
    # The success path is untouched.
    try:
        return _do()
    except CashbackError as e:
        return JSONResponse(status_code=400, content={
            "detail": str(e), "failure_reason": str(e)})


@router.get("/cashback/withdrawals")
def list_withdrawals(user: models.User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    rows = (db.query(CashbackWithdrawal)
            .filter(CashbackWithdrawal.user_id == user.id)
            .order_by(CashbackWithdrawal.id.desc()).limit(50).all())
    return {"withdrawals": [svc._withdrawal_out(w) for w in rows]}


@router.post("/cashback/transactions/{txn_id}/void")
def void_transaction(txn_id: int, body: VoidIn,
                    user: models.User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    def _do():
        t = svc.void_transaction(db, user, txn_id, body.reason)
        db.commit()
        return {"message": "返現已作廢", "transaction": svc._tx_out(db, t)}
    return _handle(_do)


@router.get("/cashback/missing")
def list_missing_cashback(limit: int = Query(50, ge=1, le=200),
                          user: models.User = Depends(get_current_user),
                          db: Session = Depends(get_db)):
    """Gap 3: the caller's own Missing Cashback appeals, newest first.

    Four states (models_cashback.CashbackAppeal.status):
    submitted | under_review | resolved | rejected.
    """
    return {"appeals": svc.list_appeals(db, user.id, limit=limit)}


@router.post("/cashback/missing")
def missing_cashback(body: MissingIn,
                     user: models.User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    def _do():
        a = svc.create_appeal(db, user, body.merchant_id, body.order_no,
                              body.spent_at, body.amount,
                              evidence=body.evidence, note=body.note)
        db.commit()
        return {"message": "申訴已提交，調查期最長 120 天",
                "appeal": svc.appeal_out(a, db)}
    return _handle(_do)
