from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
from app.billing import VerifiedCreditGrant, apply_verified_credit_grant, process_stripe_event
from app.ledger import balance, finalize, grant, release, reserve
from app.models import BillingEvent, CreditPurchase, Job, Subscription, User
from app.config import settings


def test_reservation_finalize_and_release_are_ledgered():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(email="ledger@example.com", password_hash="hash")
        db.add(user)
        db.flush()
        grant(db, user.id, 20, reference_key="test-grant")
        job = Job(user_id=user.id, media_asset_id="asset", operation="noise", idempotency_key="job-1", estimate_credits=7, reserved_credits=7)
        db.add(job)
        db.flush()
        reserve(db, user, job, 7)
        db.commit()
        assert balance(db, user.id) == 13
        finalize(db, job, 5)
        db.commit()
        assert balance(db, user.id) == 13
        assert job.actual_credits == 5

        job2 = Job(user_id=user.id, media_asset_id="asset", operation="noise", idempotency_key="job-2", estimate_credits=4, reserved_credits=4)
        db.add(job2)
        db.flush()
        reserve(db, user, job2, 4)
        db.commit()
        release(db, job2)
        db.commit()
        assert balance(db, user.id) == 13

        job3 = Job(user_id=user.id, media_asset_id="asset", operation="noise", idempotency_key="job-3", estimate_credits=4, reserved_credits=4, state="cancelled")
        db.add(job3)
        db.flush()
        reserve(db, user, job3, 4)
        db.commit()
        assert balance(db, user.id) == 9
        finalize(db, job3, 4)
        db.commit()
        assert balance(db, user.id) == 9

        reserve(db, user, job3, 4)
        db.commit()
        assert balance(db, user.id) == 5
        finalize(db, job3, 4)
        db.commit()
        assert balance(db, user.id) == 5


def test_future_billing_grant_bridge_is_idempotent():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(email="billing@example.com", password_hash="hash")
        db.add(user)
        db.flush()
        verified = VerifiedCreditGrant(user_id=user.id, credits=25, reference_key="future-provider:event-1", metadata={"provider": "future"})
        first = apply_verified_credit_grant(db, verified)
        second = apply_verified_credit_grant(db, verified)
        db.commit()
        assert first.id == second.id
        assert balance(db, user.id) == 25
        assert first.entry_type == "billing_grant"


def test_stripe_credit_checkout_is_idempotent():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(email="stripe-credit@example.com", password_hash="hash", stripe_customer_id="cus_credit")
        db.add(user)
        db.flush()
        event = {
            "id": "evt_credit_1",
            "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_credit_1", "mode": "payment", "payment_status": "paid", "customer": "cus_credit", "metadata": {"kind": "credits", "pack_key": "starter", "user_id": user.id}, "amount_total": 1200, "currency": "usd", "payment_intent": "pi_credit_1"}},
        }
        assert process_stripe_event(db, event)["processed"] is True
        assert balance(db, user.id) == 100
        assert db.scalar(select(CreditPurchase).where(CreditPurchase.provider_checkout_session_id == "cs_credit_1")).status == "paid"
        duplicate = process_stripe_event(db, event)
        assert duplicate["duplicate"] is True
        assert balance(db, user.id) == 100
        assert db.scalar(select(BillingEvent).where(BillingEvent.provider_event_id == "evt_credit_1")) is not None


def test_stripe_credit_refund_reverses_granted_credits_once():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(email="stripe-refund@example.com", password_hash="hash", stripe_customer_id="cus_refund")
        db.add(user)
        db.flush()
        process_stripe_event(db, {"id": "evt_refund_credit", "type": "checkout.session.completed", "data": {"object": {"id": "cs_refund", "mode": "payment", "payment_status": "paid", "customer": "cus_refund", "metadata": {"kind": "credits", "pack_key": "starter", "user_id": user.id}, "amount_total": 1200, "currency": "usd", "payment_intent": "pi_refund"}}})
        process_stripe_event(db, {"id": "evt_charge_refunded", "type": "charge.refunded", "data": {"object": {"id": "ch_refund", "customer": "cus_refund", "payment_intent": "pi_refund", "amount": 1200, "amount_refunded": 1200, "refunded": True}}})
        assert balance(db, user.id) == 0
        purchase = db.scalar(select(CreditPurchase).where(CreditPurchase.provider_payment_intent_id == "pi_refund"))
        assert purchase.status == "refunded"
        assert purchase.refunded_credits == 100
        duplicate = process_stripe_event(db, {"id": "evt_charge_refunded_duplicate", "type": "charge.refunded", "data": {"object": {"id": "ch_refund", "customer": "cus_refund", "payment_intent": "pi_refund", "amount": 1200, "amount_refunded": 1200, "refunded": True}}})
        assert duplicate["processed"] is True
        assert balance(db, user.id) == 0


def test_stripe_partial_credit_refund_reverses_only_the_refunded_share():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(email="stripe-partial-refund@example.com", password_hash="hash", stripe_customer_id="cus_partial_refund")
        db.add(user)
        db.flush()
        process_stripe_event(db, {"id": "evt_partial_purchase", "type": "checkout.session.completed", "data": {"object": {"id": "cs_partial", "mode": "payment", "payment_status": "paid", "customer": "cus_partial_refund", "metadata": {"kind": "credits", "pack_key": "starter", "user_id": user.id}, "amount_total": 1200, "currency": "usd", "payment_intent": "pi_partial"}}})
        process_stripe_event(db, {"id": "evt_partial_refund", "type": "charge.refunded", "data": {"object": {"id": "ch_partial", "customer": "cus_partial_refund", "payment_intent": "pi_partial", "amount": 1200, "amount_refunded": 600, "refunded": False}}})
        assert balance(db, user.id) == 50
        purchase = db.scalar(select(CreditPurchase).where(CreditPurchase.provider_payment_intent_id == "pi_partial"))
        assert purchase.status == "partially_refunded"
        assert purchase.refunded_credits == 50


def test_stripe_subscription_and_invoice_grant_monthly_credits(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    previous = settings.stripe_price_creator
    object.__setattr__(settings, "stripe_price_creator", "price_creator")
    try:
        with Session(engine) as db:
            user = User(email="stripe-subscription@example.com", password_hash="hash", stripe_customer_id="cus_subscription")
            db.add(user)
            db.flush()
            subscription = {"id": "sub_1", "customer": "cus_subscription", "status": "active", "current_period_end": 1_900_000_000, "items": {"data": [{"price": {"id": "price_creator"}}]}}
            process_stripe_event(db, {"id": "evt_sub_1", "type": "customer.subscription.updated", "data": {"object": subscription}})
            assert user.plan_key == "creator"
            assert db.scalar(select(Subscription).where(Subscription.provider_subscription_id == "sub_1")).plan_key == "creator"
            process_stripe_event(db, {"id": "evt_invoice_1", "type": "invoice.paid", "data": {"object": {"id": "in_1", "customer": "cus_subscription", "subscription": "sub_1", "period_end": 1_900_000_000}}})
            assert balance(db, user.id) == 500
            process_stripe_event(db, {"id": "evt_invoice_2", "type": "invoice.paid", "data": {"object": {"id": "in_1", "customer": "cus_subscription", "subscription": "sub_1", "period_end": 1_900_000_000}}})
            assert balance(db, user.id) == 500
    finally:
        object.__setattr__(settings, "stripe_price_creator", previous)
