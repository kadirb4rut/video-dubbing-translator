from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .ledger import adjust as ledger_adjust
from .ledger import grant as ledger_grant
from .models import BillingEvent, CreditPurchase, Subscription, User


@dataclass(frozen=True)
class Plan:
    key: str
    name: str
    monthly_credits: int
    max_concurrent_jobs: int
    max_voice_profiles: int


PLANS = {
    "free": Plan("free", "Free", 30, 1, 1),
    "creator": Plan("creator", "Creator", 500, 2, 5),
    "pro": Plan("pro", "Pro", 1600, 4, 20),
    "studio": Plan("studio", "Studio", 5000, 8, 100),
}

# Credit quantities are server-side product configuration. Stripe price IDs
# are deployment configuration; the browser can select only a known key.
CREDIT_PACKS = {"starter": 100, "growth": 500, "scale": 1500}
PLAN_PRICE_SETTINGS = {"creator": "stripe_price_creator", "pro": "stripe_price_pro", "studio": "stripe_price_studio"}
CREDIT_PRICE_SETTINGS = {"starter": "stripe_price_credits_starter", "growth": "stripe_price_credits_growth", "scale": "stripe_price_credits_scale"}


@dataclass(frozen=True)
class VerifiedCreditGrant:
    """Provider-neutral result after a verified credit event."""

    user_id: str
    credits: int
    reference_key: str
    metadata: dict


class BillingProvider(Protocol):
    name: str


class BillingNotConfigured(RuntimeError):
    pass


class DisabledBillingProvider:
    name = "disabled"


class StripeBillingProvider:
    name = "stripe"
    api_version = "2026-02-25.clover"

    def __init__(self, stripe_module: Any | None = None):
        if stripe_module is None:
            if not settings.stripe_secret_key:
                raise BillingNotConfigured("STRIPE_SECRET_KEY is required")
            try:
                import stripe as stripe_module  # type: ignore[import-not-found]
            except ImportError as exc:
                raise BillingNotConfigured("The Stripe SDK is not installed") from exc
        self.stripe = stripe_module
        self.stripe.api_key = settings.stripe_secret_key
        self.stripe.api_version = self.api_version

    def create_checkout_session(self, db: Session, user: User, *, kind: str, key: str) -> Any:
        if kind == "subscription":
            price_id = _configured_price(PLAN_PRICE_SETTINGS, key)
            metadata = {"kind": kind, "plan_key": key, "user_id": user.id}
            mode = "subscription"
            extra = {"subscription_data": {"metadata": metadata}}
        elif kind == "credits":
            if key not in CREDIT_PACKS:
                raise ValueError("Unknown credit pack")
            price_id = _configured_price(CREDIT_PRICE_SETTINGS, key)
            metadata = {"kind": kind, "pack_key": key, "user_id": user.id}
            mode = "payment"
            extra = {"payment_intent_data": {"metadata": metadata}}
        else:
            raise ValueError("Unknown billing checkout kind")

        customer_id = user.stripe_customer_id
        if not customer_id:
            customer_id = _object_id(self.stripe.Customer.create(email=user.email, metadata={"user_id": user.id}))
            if not customer_id:
                raise BillingNotConfigured("Stripe did not return a customer ID")
            user.stripe_customer_id = customer_id

        return self.stripe.checkout.Session.create(
            mode=mode,
            customer=customer_id,
            client_reference_id=user.id,
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=settings.stripe_success_url,
            cancel_url=settings.stripe_cancel_url,
            metadata=metadata,
            **extra,
        )

    def create_billing_portal_session(self, user: User) -> Any:
        if not user.stripe_customer_id:
            raise ValueError("No Stripe customer exists for this account")
        return self.stripe.billing_portal.Session.create(customer=user.stripe_customer_id, return_url=settings.stripe_billing_portal_return_url)

    def verify_webhook(self, payload: bytes, signature: str) -> Any:
        if not settings.stripe_webhook_secret:
            raise BillingNotConfigured("STRIPE_WEBHOOK_SECRET is required")
        return self.stripe.Webhook.construct_event(payload, signature, settings.stripe_webhook_secret)


def billing_provider() -> BillingProvider:
    return StripeBillingProvider() if settings.stripe_secret_key else DisabledBillingProvider()


def apply_verified_credit_grant(db: Session, grant: VerifiedCreditGrant):
    """Apply a verified provider grant through the auditable ledger."""
    return ledger_grant(db, grant.user_id, grant.credits, reference_key=grant.reference_key, entry_type="billing_grant", metadata=grant.metadata)


def _refunded_credit_total(purchase: CreditPurchase, charge: Any) -> int | None:
    if _get(charge, "refunded") is True:
        return purchase.credits
    refunded_minor = _get(charge, "amount_refunded")
    base_amount = purchase.amount_minor or _get(charge, "amount")
    if refunded_minor is None or not base_amount:
        return None
    try:
        return min(purchase.credits, max(0, (purchase.credits * int(refunded_minor) + int(base_amount) - 1) // int(base_amount)))
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _handle_credit_refund(db: Session, charge: Any, event_id: str) -> None:
    payment_intent_id = _object_id(_get(charge, "payment_intent"))
    if not payment_intent_id:
        return
    purchase = db.scalar(select(CreditPurchase).where(CreditPurchase.provider_payment_intent_id == payment_intent_id))
    if not purchase:
        return
    target = _refunded_credit_total(purchase, charge)
    if target is None or target <= purchase.refunded_credits:
        return
    delta = target - purchase.refunded_credits
    charge_id = _object_id(charge) or event_id
    ledger_adjust(
        db,
        purchase.user_id,
        -delta,
        reference_key=f"stripe:refund:{charge_id}:{target}",
        entry_type="billing_refund",
        metadata={"provider": "stripe", "charge_id": charge_id, "payment_intent_id": payment_intent_id, "credits": delta},
    )
    purchase.refunded_credits = target
    purchase.status = "refunded" if target == purchase.credits else "partially_refunded"


def plan(key: str) -> Plan:
    return PLANS.get(key, PLANS["free"])


def _configured_price(mapping: dict[str, str], key: str) -> str:
    setting_name = mapping.get(key)
    price_id = getattr(settings, setting_name, None) if setting_name else None
    if not price_id:
        raise BillingNotConfigured(f"A Stripe price ID is not configured for {key}")
    return price_id


def _get(obj: Any, key: str, default: Any = None) -> Any:
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def _object_id(obj: Any) -> str | None:
    value = _get(obj, "id")
    if value:
        return str(value)
    return str(obj) if isinstance(obj, str) else None


def _metadata(obj: Any) -> dict[str, str]:
    raw = _get(obj, "metadata", {}) or {}
    return {str(key): str(value) for key, value in raw.items()} if isinstance(raw, dict) else {}


def _event_object(event: Any) -> Any:
    return _get(_get(event, "data", {}), "object", {})


def _event_id(event: Any) -> str | None:
    value = _get(event, "id")
    return str(value) if value else None


def _period_end(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc) if value is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _price_id(subscription: Any) -> str | None:
    data = _get(_get(subscription, "items", {}), "data", []) or []
    return _object_id(_get(data[0], "price", {})) if data else None


def _invoice_price_id(invoice: Any) -> str | None:
    lines = _get(_get(invoice, "lines", {}), "data", []) or []
    return _object_id(_get(lines[0], "price", {})) if lines else None


def _user_for_object(db: Session, obj: Any, metadata: dict[str, str] | None = None) -> User | None:
    metadata = metadata or _metadata(obj)
    if metadata.get("user_id"):
        user = db.get(User, metadata["user_id"])
        if user:
            return user
    customer_id = _object_id(_get(obj, "customer"))
    return db.scalar(select(User).where(User.stripe_customer_id == customer_id)) if customer_id else None


def _plan_for_price(price_id: str | None) -> str | None:
    if not price_id:
        return None
    return next((key for key, setting_name in PLAN_PRICE_SETTINGS.items() if price_id == getattr(settings, setting_name, None)), None)


def _sync_subscription(db: Session, data: Any) -> Subscription | None:
    provider_id = _object_id(data)
    if not provider_id:
        return None
    user = _user_for_object(db, data)
    if not user:
        return None
    customer_id = _object_id(_get(data, "customer")) or user.stripe_customer_id
    if not customer_id:
        return None
    row = db.scalar(select(Subscription).where(Subscription.provider_subscription_id == provider_id))
    if not row:
        row = Subscription(user_id=user.id, provider_customer_id=customer_id, provider_subscription_id=provider_id)
        db.add(row)
    row.user_id = user.id
    row.provider_customer_id = customer_id
    row.price_id = _price_id(data)
    row.plan_key = _plan_for_price(row.price_id) or row.plan_key or "free"
    row.status = str(_get(data, "status", row.status))
    row.current_period_end = _period_end(_get(data, "current_period_end"))
    row.cancel_at_period_end = bool(_get(data, "cancel_at_period_end", False))
    user.stripe_customer_id = customer_id
    if row.plan_key != "free" and row.status in {"active", "trialing", "past_due"}:
        user.plan_key = row.plan_key
    elif row.status in {"canceled", "unpaid", "incomplete_expired"}:
        user.plan_key = "free"
    return row


def _handle_credit_checkout(db: Session, data: Any, *, paid: bool) -> None:
    metadata = _metadata(data)
    if metadata.get("kind") != "credits":
        return
    user = _user_for_object(db, data, metadata)
    session_id = _object_id(data)
    pack_key = metadata.get("pack_key", "")
    if not user or not session_id or pack_key not in CREDIT_PACKS:
        return
    purchase = db.scalar(select(CreditPurchase).where(CreditPurchase.provider_checkout_session_id == session_id))
    if not purchase:
        purchase = CreditPurchase(user_id=user.id, provider_checkout_session_id=session_id, provider_payment_intent_id=_object_id(_get(data, "payment_intent")), pack_key=pack_key, credits=CREDIT_PACKS[pack_key], amount_minor=_get(data, "amount_total"), currency=_get(data, "currency"), status="paid" if paid else "pending", metadata_json=json.dumps({"provider": "stripe"}))
        db.add(purchase)
    elif paid:
        purchase.status = "paid"
    if paid:
        apply_verified_credit_grant(db, VerifiedCreditGrant(user.id, CREDIT_PACKS[pack_key], f"stripe:checkout:{session_id}", {"provider": "stripe", "purchase_id": session_id, "pack_key": pack_key}))


def _handle_invoice_paid(db: Session, invoice: Any, event_id: str) -> None:
    subscription_id = _object_id(_get(invoice, "subscription"))
    customer_id = _object_id(_get(invoice, "customer"))
    row = db.scalar(select(Subscription).where(Subscription.provider_subscription_id == subscription_id)) if subscription_id else None
    user = db.get(User, row.user_id) if row else db.scalar(select(User).where(User.stripe_customer_id == customer_id))
    if not user:
        return
    subscription_details = _get(invoice, "subscription_details", {}) or {}
    plan_key = row.plan_key if row else _metadata(subscription_details).get("plan_key") or _plan_for_price(_invoice_price_id(invoice))
    if row is None and subscription_id and plan_key in PLANS and plan_key != "free" and customer_id:
        row = Subscription(
            user_id=user.id,
            provider_customer_id=customer_id,
            provider_subscription_id=subscription_id,
            price_id=_invoice_price_id(invoice),
            plan_key=plan_key,
            status="active",
            current_period_end=_period_end(_get(invoice, "period_end")),
        )
        db.add(row)
    if row:
        row.status = "active"
        row.current_period_end = _period_end(_get(invoice, "period_end")) or row.current_period_end
    if not plan_key or plan_key == "free":
        return
    apply_verified_credit_grant(db, VerifiedCreditGrant(user.id, plan(plan_key).monthly_credits, f"stripe:invoice:{_object_id(invoice) or event_id}", {"provider": "stripe", "invoice_id": _object_id(invoice) or event_id, "plan": plan_key}))


def process_stripe_event(db: Session, event: Any) -> dict[str, Any]:
    """Apply one verified Stripe event exactly once."""
    event_id = _event_id(event)
    event_type = str(_get(event, "type", ""))
    if not event_id or not event_type:
        raise ValueError("Stripe event is missing id or type")
    if db.scalar(select(BillingEvent).where(BillingEvent.provider_event_id == event_id)):
        return {"processed": False, "duplicate": True, "event_id": event_id}

    obj = _event_object(event)
    db.add(BillingEvent(provider_event_id=event_id, event_type=event_type, object_id=_object_id(obj), metadata_json=json.dumps({"provider": "stripe"})))
    if event_type in {"checkout.session.completed", "checkout.session.async_payment_succeeded"}:
        if _get(obj, "mode") == "payment":
            paid = event_type.endswith("async_payment_succeeded") or _get(obj, "payment_status") == "paid"
            _handle_credit_checkout(db, obj, paid=paid)
        elif _get(obj, "mode") == "subscription":
            user = _user_for_object(db, obj)
            customer_id = _object_id(_get(obj, "customer"))
            if user and customer_id:
                user.stripe_customer_id = customer_id
    elif event_type == "checkout.session.expired":
        session_id = _object_id(obj)
        purchase = db.scalar(select(CreditPurchase).where(CreditPurchase.provider_checkout_session_id == session_id)) if session_id else None
        if purchase and purchase.status == "pending":
            purchase.status = "expired"
    elif event_type.startswith("customer.subscription."):
        row = _sync_subscription(db, obj)
        if row and event_type.endswith("deleted"):
            row.status = "canceled"
            user = db.get(User, row.user_id)
            if user:
                user.plan_key = "free"
    elif event_type == "invoice.paid":
        _handle_invoice_paid(db, obj, event_id)
    elif event_type == "invoice.payment_failed":
        subscription_id = _object_id(_get(obj, "subscription"))
        row = db.scalar(select(Subscription).where(Subscription.provider_subscription_id == subscription_id)) if subscription_id else None
        if row:
            row.status = "past_due"
    elif event_type == "charge.refunded":
        _handle_credit_refund(db, obj, event_id)
    db.commit()
    return {"processed": True, "duplicate": False, "event_id": event_id, "event_type": event_type}
