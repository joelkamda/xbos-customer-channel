from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..payment_experience import (
    CanonicalPaymentRequestProjection,
    NextAction,
    NextActionType,
    PaymentMethodCode,
    PaymentMethodPresentation,
    PaymentRequestAuthorityMismatch,
)


@dataclass(frozen=True, slots=True)
class _RegisteredPaymentPolicy:
    correlation_ref: str
    amount: Decimal
    currency: str
    permitted_methods: tuple[PaymentMethodCode, ...]
    pay_at_counter_permitted: bool


class FakeXBOSPaymentRequestClient:
    """Deterministic XBOS order/amount/method-policy fixture. It owns no Channel payment ledger."""

    def __init__(self) -> None:
        self._policies: dict[str, _RegisteredPaymentPolicy] = {}

    def register_order_payment_policy(
        self,
        *,
        order_ref: str,
        correlation_ref: str,
        canonical_amount: Decimal,
        currency: str,
        permitted_methods: tuple[PaymentMethodCode, ...],
        pay_at_counter_permitted: bool = False,
    ) -> None:
        self._policies[order_ref] = _RegisteredPaymentPolicy(
            correlation_ref=correlation_ref,
            amount=canonical_amount,
            currency=currency,
            permitted_methods=permitted_methods,
            pay_at_counter_permitted=pay_at_counter_permitted,
        )

    def get_payment_request(
        self,
        *,
        order_ref: str,
        correlation_ref: str,
    ) -> CanonicalPaymentRequestProjection:
        try:
            policy = self._policies[order_ref]
        except KeyError:
            raise KeyError("canonical_order_payment_policy_not_found") from None
        if policy.correlation_ref != correlation_ref:
            raise PaymentRequestAuthorityMismatch("canonical_order_correlation_mismatch")

        methods = tuple(
            self._presentation(method)
            for method in policy.permitted_methods
            if method is not PaymentMethodCode.PAY_AT_COUNTER or policy.pay_at_counter_permitted
        )
        return CanonicalPaymentRequestProjection(
            payment_request_ref=f"payment-request:fixture:{order_ref}",
            order_ref=order_ref,
            correlation_ref=correlation_ref,
            canonical_amount=policy.amount,
            currency=policy.currency,
            methods=methods,
            policy_ref=f"payment-policy:fixture:{order_ref}",
            evidence_ref=f"payment-request-evidence:fixture:{order_ref}",
        )

    @staticmethod
    def _presentation(method: PaymentMethodCode) -> PaymentMethodPresentation:
        labels = {
            PaymentMethodCode.XAFPAY_WALLET: "XafPay Wallet",
            PaymentMethodCode.MTN_MOBILE_MONEY: "MTN Mobile Money",
            PaymentMethodCode.ORANGE_MONEY: "Orange Money",
            PaymentMethodCode.CARD: "Card",
            PaymentMethodCode.PAY_AT_COUNTER: "Pay at counter",
        }
        actions = {
            PaymentMethodCode.XAFPAY_WALLET: NextAction(NextActionType.OPEN_WALLET, "wallet-ui:fixture"),
            PaymentMethodCode.MTN_MOBILE_MONEY: NextAction(NextActionType.AWAIT_PUSH, "push-ui:fixture:mtn"),
            PaymentMethodCode.ORANGE_MONEY: NextAction(NextActionType.AWAIT_PUSH, "push-ui:fixture:orange"),
            PaymentMethodCode.CARD: NextAction(NextActionType.OPEN_URL, "checkout-ui:fixture:card"),
            PaymentMethodCode.PAY_AT_COUNTER: NextAction(NextActionType.NONE, None),
        }
        return PaymentMethodPresentation(method=method, label=labels[method], next_action=actions[method])
