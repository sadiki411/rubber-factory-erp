"""Review-only prototype: no route, no model writes, no real confirmation.

Uses the existing planner without changing its allocation rules. The in-memory
receipt demonstrates the intended retry contract only; it is NOT a durable
idempotency or cross-process concurrency implementation.
"""
import hashlib
import json

from quality.services import build_order_allocation_plan, serialize_order_allocation_plan


class ReviewChanged(ValueError):
    def __init__(self, latest):
        self.latest = latest
        super().__init__("订单余额或分摊已变化，请复核新预览。未确认出货。")


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def preview(source_order, quantity, card_numbers):
    plan = serialize_order_allocation_plan(build_order_allocation_plan(source_order, quantity))
    result = {"mode": "ISOLATED_REVIEW_ONLY", "source_order_id": source_order.pk,
              "card_numbers": list(card_numbers), "plan": plan}
    return {**result, "review_version": _digest(result)}


class SimulatedConfirmation:
    def __init__(self):
        self.receipts = {}

    def confirm(self, key, reviewed, source_order):
        if not key:
            raise ValueError("需要重试标识。")
        request_digest = _digest(reviewed)
        if key in self.receipts:
            stored = self.receipts[key]
            if stored["request_digest"] != request_digest:
                raise ValueError("同一重试标识不能提交不同内容。")
            return stored
        latest = preview(source_order, reviewed["plan"]["requested_quantity"], reviewed["card_numbers"])
        if latest["review_version"] != reviewed.get("review_version"):
            raise ReviewChanged(latest)
        receipt = {"mode": "SIMULATED_ONLY_NO_SHIPMENT_CREATED", "request_digest": request_digest, "review": latest}
        self.receipts[key] = receipt
        return receipt
