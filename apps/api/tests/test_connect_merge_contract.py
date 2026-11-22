"""Contracts retained when integrating the original Connect branch."""
from app import config, scheduler


def test_connect_and_scheduler_status_explain_disabled_jobs(monkeypatch):
    monkeypatch.setattr(config, "STRIPE_CONNECT_ENABLED_RAW", "0")
    status = config.connect_enabled()
    assert not status["enabled"] and status["configured_explicitly"]
    assert "关闭" in status["reason"]
    monkeypatch.setattr(config, "SCHEDULER_ENABLED_RAW", "0")
    status = config.scheduler_status()
    assert not status["enabled"] and "商户转账" in status["reason"]
    assert "积分过期" in status["reason"]
    assert status["connect_batch_size"] == config.SCHEDULER_CONNECT_BATCH_SIZE


def test_all_jobs_pass_batch_size_to_connect_and_cashback(monkeypatch):
    observed = {}
    class Session:
        def close(self):
            pass
    def ordinary(db):
        return {"failed": 0}
    for name in ("expired-packages", "bargain-expire", "payment-timeouts",
                 "coupon-expiry", "late-payment-refunds", "listing-schedule", "points-expire"):
        monkeypatch.setitem(scheduler.JOB_RUNNERS, name, ordinary)
    def connect(db, batch_size=None):
        observed["connect"] = batch_size
        return {"failed": 0}
    def cashback(db, batch_size=None):
        observed["cashback"] = batch_size
        return {"failed": 0}
    monkeypatch.setattr(scheduler, "run_connect_transfers", connect)
    monkeypatch.setattr(scheduler, "run_cashback_sweep", cashback)
    scheduler.run_all_jobs(session_factory=Session, batch_size=7)
    assert observed == {"connect": 7, "cashback": 7}
