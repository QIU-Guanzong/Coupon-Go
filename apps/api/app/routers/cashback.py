"""Line D — cashback routes (返现 2B).

Paths follow FEATURE_RESEARCH_20261116.md §12 (返现 section):
  GET  /cashback                  返现余额 + 明细（按状态分组）
  GET  /cashback/transactions     返现流水（含预计确认时间）
  POST /cashback/withdraw         申请提现（FPS / 银行）
  GET  /cashback/withdrawals      提现记录
  POST /cashback/missing          Missing Cashback 申诉

Plus two D-line endpoints the report's chain needs but does not name:
  POST /cashback/record           消费记返现（核销记录为准的入账口；
                                  线下 POS / 商户端日后接同一入口）
  POST /cashback/transactions/{id}/void   取消 / 退货作废

All amounts are HK$. Cashback is a separate ledger from points.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..deps import get_current_user
from .. import cashback_service as svc
from ..cashback_service import CashbackError
from ..models_cashback import CashbackTransaction, CashbackWithdrawal

router = APIRouter()


class RecordIn(BaseModel):
    merchant_id: int
    amount: float = Field(gt=0)          # 消费金额 HK$（实付价）
    channel: str = "dinein"              # dinein | online
    rate: float | None = None            # 不填用默认比例（线下餐饮 5%）
    redemption_id: int | None = None     # 核销记录（追踪以核销记录为准）


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
            "transactions": [svc._tx_out(db, t) for t in txns],
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


@router.post("/cashback/record")
def record_spend(body: RecordIn, user: models.User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    def _do():
        txn = svc.record_spend(db, user, body.merchant_id, body.amount,
                               body.channel, rate=body.rate,
                               redemption_id=body.redemption_id)
        db.commit()
        return {"message": "返現已記錄，待確認", "transaction": svc._tx_out(db, txn),
                **svc.balances(db, user.id)}
    return _handle(_do)


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
    return _handle(_do)


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
