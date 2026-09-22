from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from quality.models import (
    ProcessCard,
    ProcessCardUnitBinding,
    QualityOrder,
    QualityReworkCase,
    QualityReworkAttempt,
    QualityShipmentBatch,
    QualityShipmentBatchRevision,
    QualityShipmentLine,
    QualityShipmentOrderAllocation,
)
from quality.services import delivered_quantities_by_order

from .helpers import QualityTestMixin


class ConfirmedShipmentCorrectionApiTests(QualityTestMixin, TestCase):
    endpoint = "/api/quality/shipment-batches/"

    def create_order(self, order_no, quantity, *, due_days=10):
        return QualityOrder.objects.create(
            order_no=order_no,
            product_name=self.order.product_name,
            specification=self.order.specification,
            material=self.order.material,
            order_quantity=quantity,
            order_date=timezone.localdate(),
            due_date=timezone.localdate() + timedelta(days=due_days),
            created_by=self.user,
        )

    def create_confirmed_repeat(
        self,
        *,
        shipment_no="QS-AMEND-001",
        order=None,
        single_weight="10.000",
        unit_weight="10.00000",
        standard=1_000,
        batch_count=1,
        bindings=None,
        shipment_date=None,
        backfill_reason=None,
    ):
        target = order or self.order
        draft = self.client.post(
            self.endpoint,
            {
                "shipment_no": shipment_no,
                "shipment_date": shipment_date or timezone.localdate().isoformat(),
                "backfill_reason": backfill_reason or "",
                "order_id": target.pk,
                "specification_snapshot": target.specification,
                "material_snapshot": target.material,
                "unit_weight_g": unit_weight,
                "single_batch_net_weight_kg": single_weight,
                "process_card_shipment_quantity": standard,
                "product_batch_count": batch_count,
                "lines": [{"order_id": target.pk}],
            },
            format="json",
        )
        self.assertEqual(draft.status_code, 201, draft.content)
        confirm_payload = (
            {"process_card_bindings": bindings} if bindings is not None else {}
        )
        confirmed = self.client.post(
            f"{self.endpoint}{draft.json()['id']}/confirm/",
            confirm_payload,
            format="json",
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        return QualityShipmentBatch.objects.get(pk=draft.json()["id"])

    def amend(self, batch, **payload):
        values = {"amend_reason": "纠正录入错误", **payload}
        return self.client.post(
            f"{self.endpoint}{batch.pk}/amend/", values, format="json"
        )

    def create_confirmed_card_line(self):
        self.order.order_quantity = 5_000
        self.order.save(update_fields=["order_quantity", "updated_at"])
        card = ProcessCard.objects.create(
            card_no="CARD-LINE-ORIGINAL",
            qr_text="CARD-LINE-ORIGINAL",
            order=self.order,
            source_order_no=self.order.order_no,
            source_item_no=self.order.item_no,
            product_name_snapshot=self.order.product_name,
            specification_snapshot=self.order.specification,
            material_snapshot=self.order.material,
            quantity=1_000,
            unit_weight_g=Decimal("10.00000"),
            created_by=self.user,
        )
        draft = self.client.post(
            self.endpoint,
            {
                "shipment_no": "QS-AMEND-CARD-LINE",
                "shipment_date": timezone.localdate().isoformat(),
                "order_id": self.order.pk,
                "unit_weight_g": "10.00000",
                "single_batch_net_weight_kg": "10.000",
                "process_card_shipment_quantity": 1_000,
                "product_batch_count": 1,
                "lines": [
                    {
                        "process_card_id": card.pk,
                        "order_id": self.order.pk,
                        "unit_weight_g_snapshot": "10.00000",
                        "single_batch_net_weight_kg": "10.000",
                        "process_card_shipment_quantity": 1_000,
                        "product_batch_count": 1,
                    }
                ],
            },
            format="json",
        )
        self.assertEqual(draft.status_code, 201, draft.content)
        confirmed = self.client.post(
            f"{self.endpoint}{draft.json()['id']}/confirm/", {}, format="json"
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        batch = QualityShipmentBatch.objects.get(pk=draft.json()["id"])
        self.assertTrue(
            batch.process_card_bindings.filter(process_card=card).exists()
        )
        return batch, card

    def test_amend_recalculates_weight_quantity_number_and_cross_order_allocation(self):
        self.order.order_quantity = 1_000
        self.order.save(update_fields=["order_quantity", "updated_at"])
        second = self.create_order("ORD-AMEND-NEXT", 1_000, due_days=1)
        batch = self.create_confirmed_repeat(batch_count=2)
        old_line_id = batch.lines.get().pk
        self.assertEqual(
            delivered_quantities_by_order([self.order.pk, second.pk]),
            {self.order.pk: 1_000, second.pk: 1_000},
        )

        response = self.amend(
            batch,
            shipment_no="QS-AMEND-CORRECTED",
            unit_weight_g="8.00000",
            single_batch_net_weight_kg="4.000",
            process_card_shipment_quantity=500,
            product_batch_count=1,
            lines=[{"order_id": self.order.pk}],
            process_card_bindings=[],
        )
        self.assertEqual(response.status_code, 200, response.content)
        batch.refresh_from_db()
        self.assertEqual(batch.status, QualityShipmentBatch.Status.CONFIRMED)
        self.assertEqual(batch.shipment_no, "QS-AMEND-CORRECTED")
        self.assertEqual(batch.unit_weight_g, Decimal("8.00000"))
        self.assertEqual(batch.single_batch_net_weight_kg, Decimal("4.000"))
        self.assertEqual(batch.product_batch_count, 1)
        self.assertEqual(batch.process_card_shipment_quantity, 500)
        line = batch.lines.get()
        self.assertNotEqual(line.pk, old_line_id)
        self.assertEqual(line.net_weight_kg, Decimal("4.000"))
        self.assertEqual(line.piece_quantity, 500)
        self.assertEqual(
            list(
                QualityShipmentOrderAllocation.objects.filter(
                    shipment_line__batch=batch
                ).values_list("order_id", "piece_quantity")
            ),
            [(self.order.pk, 500)],
        )
        self.assertEqual(
            delivered_quantities_by_order([self.order.pk, second.pk]),
            {self.order.pk: 500, second.pk: 0},
        )
        revision = QualityShipmentBatchRevision.objects.get(batch=batch)
        self.assertEqual(revision.action, QualityShipmentBatchRevision.Action.AMEND)
        self.assertEqual(revision.before_snapshot["shipment_no"], "QS-AMEND-001")
        self.assertEqual(
            revision.after_snapshot["shipment_no"], "QS-AMEND-CORRECTED"
        )

    def test_amend_historical_shipment_accepts_and_persists_backfill_reason(self):
        historical = timezone.localdate() - timedelta(days=1)
        batch = self.create_confirmed_repeat(
            shipment_no="QS-AMEND-HISTORICAL",
            shipment_date=historical.isoformat(),
            backfill_reason="纸质出货记录次日补录",
        )
        self.assertEqual(batch.backfill_reason, "纸质出货记录次日补录")

        response = self.amend(
            batch,
            backfill_reason="修改称重后重新核对历史出货记录",
            notes="历史出货重量已按纸质记录修正",
        )

        self.assertEqual(response.status_code, 200, response.content)
        batch.refresh_from_db()
        self.assertEqual(batch.status, QualityShipmentBatch.Status.CONFIRMED)
        self.assertEqual(batch.backfill_reason, "修改称重后重新核对历史出货记录")

    def test_amend_can_preserve_replace_and_explicitly_clear_scan_bindings(self):
        self.order.order_quantity = 5_000
        self.order.save(update_fields=["order_quantity", "updated_at"])
        batch = self.create_confirmed_repeat(
            batch_count=2,
            bindings=[
                {"card_no": "CARD-AMEND-OLD-1", "shipment_unit_no": 1},
                {"card_no": "CARD-AMEND-KEEP-2", "shipment_unit_no": 2},
            ],
        )
        self.assertEqual(batch.process_card_bindings.count(), 2)

        preserved = self.amend(
            batch,
            shipment_no=batch.shipment_no,
            notes="只改备注，保留系统生成的原出货单号",
        )
        self.assertEqual(preserved.status_code, 200, preserved.content)
        self.assertEqual(
            set(batch.process_card_bindings.values_list("process_card__card_no", flat=True)),
            {"CARD-AMEND-OLD-1", "CARD-AMEND-KEEP-2"},
        )

        replaced = self.amend(
            batch,
            process_card_bindings=[
                {"card_no": "CARD-AMEND-NEW-1", "shipment_unit_no": 1},
                {"card_no": "CARD-AMEND-KEEP-2", "shipment_unit_no": 2},
            ],
        )
        self.assertEqual(replaced.status_code, 200, replaced.content)
        self.assertEqual(
            set(batch.process_card_bindings.values_list("process_card__card_no", flat=True)),
            {"CARD-AMEND-NEW-1", "CARD-AMEND-KEEP-2"},
        )
        old_card = ProcessCard.objects.get(card_no="CARD-AMEND-OLD-1")
        old_card.refresh_from_db()
        self.assertEqual(old_card.status, ProcessCard.Status.OPEN)

        cleared = self.amend(batch, process_card_bindings=[])
        self.assertEqual(cleared.status_code, 200, cleared.content)
        self.assertFalse(
            ProcessCardUnitBinding.objects.filter(shipment_batch=batch).exists()
        )
        for card_no in ("CARD-AMEND-NEW-1", "CARD-AMEND-KEEP-2"):
            self.assertEqual(
                ProcessCard.objects.get(card_no=card_no).status,
                ProcessCard.Status.OPEN,
            )
        self.assertEqual(batch.revisions.count(), 3)

    def test_process_card_line_binding_can_be_replaced_then_cleared(self):
        batch, original_card = self.create_confirmed_card_line()

        replaced = self.amend(
            batch,
            process_card_bindings=[
                {"card_no": "CARD-LINE-REPLACEMENT", "shipment_unit_no": 1}
            ],
        )
        self.assertEqual(replaced.status_code, 200, replaced.content)
        line = batch.lines.get()
        self.assertIsNone(line.process_card_id)
        self.assertEqual(line.order_id, self.order.pk)
        binding = batch.process_card_bindings.select_related("process_card").get()
        self.assertEqual(binding.process_card.card_no, "CARD-LINE-REPLACEMENT")
        original_card.refresh_from_db()
        self.assertEqual(original_card.status, ProcessCard.Status.OPEN)

        cleared = self.amend(batch, process_card_bindings=[])
        self.assertEqual(cleared.status_code, 200, cleared.content)
        self.assertFalse(batch.process_card_bindings.exists())
        self.assertEqual(
            ProcessCard.objects.get(card_no="CARD-LINE-REPLACEMENT").status,
            ProcessCard.Status.OPEN,
        )

    def test_failed_amend_rolls_back_original_confirmation_and_bindings(self):
        self.order.order_quantity = 5_000
        self.order.save(update_fields=["order_quantity", "updated_at"])
        batch = self.create_confirmed_repeat(
            bindings=[{"card_no": "CARD-AMEND-ROLLBACK", "shipment_unit_no": 1}]
        )
        before_line = batch.lines.get()
        before_allocation_ids = list(
            before_line.order_allocations.values_list("id", flat=True)
        )

        response = self.amend(
            batch,
            shipment_no="QS-SHOULD-ROLL-BACK",
            unit_weight_g="10.00000",
            single_batch_net_weight_kg="12.000",
            process_card_shipment_quantity=1_000,
            product_batch_count=1,
            lines=[{"order_id": self.order.pk}],
            process_card_bindings=[],
        )
        self.assertEqual(response.status_code, 400, response.content)
        batch.refresh_from_db()
        self.assertEqual(batch.status, QualityShipmentBatch.Status.CONFIRMED)
        self.assertEqual(batch.shipment_no, "QS-AMEND-001")
        self.assertEqual(batch.lines.get().pk, before_line.pk)
        self.assertEqual(
            list(batch.lines.get().order_allocations.values_list("id", flat=True)),
            before_allocation_ids,
        )
        self.assertTrue(
            ProcessCardUnitBinding.objects.filter(
                shipment_batch=batch,
                process_card__card_no="CARD-AMEND-ROLLBACK",
            ).exists()
        )
        self.assertFalse(batch.revisions.exists())

        malformed_binding = self.amend(
            batch, process_card_bindings=["CARD-WITHOUT-PHYSICAL-UNIT"]
        )
        self.assertEqual(
            malformed_binding.status_code, 400, malformed_binding.content
        )
        self.assertTrue(
            ProcessCardUnitBinding.objects.filter(
                shipment_batch=batch,
                process_card__card_no="CARD-AMEND-ROLLBACK",
            ).exists()
        )
        self.assertFalse(batch.revisions.exists())

    def test_downstream_return_is_recalculated_by_amend_but_still_blocks_void(self):
        batch = self.create_confirmed_repeat()
        returned = self.client.post(
            "/api/quality/rework-cases/",
            {
                "origin": "CUSTOMER_RETURN",
                "shipment_batch_id": batch.pk,
                "shipment_unit_no": 1,
                "reason_category": "APPEARANCE",
                "reason": "客户退回整批",
            },
            format="json",
        )
        self.assertEqual(returned.status_code, 201, returned.content)
        attempt = QualityReworkAttempt.objects.create(
            case_id=returned.json()["id"],
            rework_employee=self.reworker,
            input_quantity=1_000,
            reworked_quantity=1_000,
            recovered_quantity=900,
            scrap_quantity=50,
            input_weight_kg=Decimal("10.000"),
            reworked_weight_kg=Decimal("10.000"),
            recovered_weight_kg=Decimal("9.000"),
            scrap_weight_kg=Decimal("0.500"),
            created_by=self.user,
        )

        amended = self.amend(
            batch,
            notes="修正后同步退货",
            unit_weight_g="10.00000",
            single_batch_net_weight_kg="8.000",
            process_card_shipment_quantity=800,
            product_batch_count=1,
            lines=[{"order_id": self.order.pk}],
        )
        self.assertEqual(amended.status_code, 200, amended.content)
        case = batch.rework_cases.get()
        case.refresh_from_db()
        self.assertEqual(case.affected_quantity, 800)
        self.assertEqual(case.affected_weight_kg, Decimal("8.000"))
        self.assertEqual(case.shipment_allocations.count(), 1)
        allocation = case.shipment_allocations.get()
        self.assertEqual(allocation.piece_quantity, 800)
        self.assertEqual(allocation.net_weight_kg, Decimal("8.000"))
        attempt.refresh_from_db()
        self.assertEqual(attempt.input_quantity, 800)
        self.assertEqual(attempt.reworked_quantity, 800)
        self.assertEqual(attempt.recovered_quantity, 720)
        self.assertEqual(attempt.scrap_quantity, 40)
        self.assertEqual(attempt.input_weight_kg, Decimal("8.000"))
        self.assertEqual(attempt.reworked_weight_kg, Decimal("8.000"))
        self.assertEqual(attempt.recovered_weight_kg, Decimal("7.200"))
        self.assertEqual(attempt.scrap_weight_kg, Decimal("0.400"))
        voided = self.client.post(
            f"{self.endpoint}{batch.pk}/void-confirmed/",
            {"void_reason": "不应成功"},
            format="json",
        )
        self.assertEqual(voided.status_code, 400, voided.content)
        batch.refresh_from_db()
        self.assertEqual(batch.status, QualityShipmentBatch.Status.CONFIRMED)

    def test_amend_deletes_one_scanned_physical_shipment_and_recalculates_totals(self):
        self.order.order_quantity = 5_000
        self.order.save(update_fields=["order_quantity", "updated_at"])
        batch = self.create_confirmed_repeat(
            shipment_no="QS-AMEND-DELETE-ONE",
            batch_count=2,
            bindings=[
                {"card_no": "CARD-DELETE-KEEP", "shipment_unit_no": 1},
                {"card_no": "CARD-DELETE-REMOVE", "shipment_unit_no": 2},
            ],
        )
        line = batch.lines.get()

        response = self.amend(
            batch,
            single_batch_net_weight_kg="10.000",
            process_card_shipment_quantity=1_000,
            product_batch_count=1,
            lines=[
                {
                    "line_id": line.pk,
                    "order_id": self.order.pk,
                    "unit_weight_g_snapshot": "10.00000",
                    "single_batch_net_weight_kg": "10.000",
                    "process_card_shipment_quantity": 1_000,
                    "product_batch_count": 1,
                }
            ],
            process_card_bindings=[
                {"card_no": "CARD-DELETE-KEEP", "shipment_unit_no": 1}
            ],
            removed_shipment_units=[2],
        )

        self.assertEqual(response.status_code, 200, response.content)
        batch.refresh_from_db()
        self.assertEqual(batch.lines.count(), 1)
        self.assertEqual(batch.shipped_quantity, 1_000)
        self.assertEqual(batch.net_weight_kg, Decimal("10.000"))
        self.assertEqual(
            list(
                batch.process_card_bindings.values_list(
                    "process_card__card_no", "shipment_unit_no"
                )
            ),
            [("CARD-DELETE-KEEP", 1)],
        )
        self.assertEqual(
            ProcessCard.objects.get(card_no="CARD-DELETE-REMOVE").status,
            ProcessCard.Status.OPEN,
        )
        revision = batch.revisions.get()
        self.assertEqual(len(revision.before_snapshot["process_card_bindings"]), 2)
        self.assertEqual(len(revision.after_snapshot["process_card_bindings"]), 1)

    def test_amend_uses_line_identity_when_an_earlier_line_is_deleted(self):
        self.order.order_quantity = 5_000
        self.order.save(update_fields=["order_quantity", "updated_at"])
        cards = [
            ProcessCard.objects.create(
                card_no=f"CARD-LINE-IDENTITY-{index}",
                qr_text=f"CARD-LINE-IDENTITY-{index}",
                order=self.order,
                quantity=100,
                unit_weight_g=Decimal("10.00000"),
                created_by=self.user,
            )
            for index in (1, 2)
        ]
        draft = self.client.post(
            self.endpoint,
            {
                "shipment_no": "QS-AMEND-LINE-IDENTITY",
                "shipment_date": timezone.localdate().isoformat(),
                "order_id": self.order.pk,
                "lines": [
                    {
                        "process_card_id": card.pk,
                        "order_id": self.order.pk,
                        "net_weight_kg": "1.000",
                        "piece_quantity": 100,
                    }
                    for card in cards
                ],
            },
            format="json",
        )
        self.assertEqual(draft.status_code, 201, draft.content)
        confirmed = self.client.post(
            f"{self.endpoint}{draft.json()['id']}/confirm/",
            {},
            format="json",
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        batch = QualityShipmentBatch.objects.get(pk=draft.json()["id"])
        original_lines = list(batch.lines.order_by("id"))
        returned = self.client.post(
            "/api/quality/rework-cases/",
            {
                "origin": "CUSTOMER_RETURN",
                "shipment_line_id": original_lines[1].pk,
                "affected_quantity": 40,
                "affected_weight_kg": "0.400",
                "reason_category": "APPEARANCE",
                "reason": "第二张流程卡部分退货",
            },
            format="json",
        )
        self.assertEqual(returned.status_code, 201, returned.content)
        original_case = QualityReworkCase.objects.get(pk=returned.json()["id"])
        self.assertIsNone(original_case.shipment_unit_no)
        self.assertEqual(original_case.shipment_line_id, original_lines[1].pk)

        response = self.amend(
            batch,
            lines=[
                {
                    "line_id": original_lines[1].pk,
                    "process_card_id": cards[1].pk,
                    "order_id": self.order.pk,
                    "net_weight_kg": "1.000",
                    "piece_quantity": 100,
                }
            ],
            process_card_bindings=[
                {"card_no": cards[1].card_no, "shipment_unit_no": 1}
            ],
            removed_shipment_units=[1],
        )

        self.assertEqual(response.status_code, 200, response.content)
        case = QualityReworkCase.objects.get(pk=returned.json()["id"])
        remaining_line = batch.lines.get()
        self.assertEqual(case.shipment_line_id, remaining_line.pk)
        self.assertNotEqual(case.status, QualityReworkCase.Status.CANCELLED)
        self.assertEqual(case.affected_quantity, 40)
        self.assertEqual(case.affected_weight_kg, Decimal("0.400"))
        self.assertEqual(case.shipment_allocations.get().shipment_line_id, remaining_line.pk)

    def test_void_confirmed_restores_order_and_card_balance_and_is_idempotent(self):
        self.order.order_quantity = 1_000
        self.order.save(update_fields=["order_quantity", "updated_at"])
        batch = self.create_confirmed_repeat(
            bindings=[{"card_no": "CARD-VOID-CONFIRMED", "shipment_unit_no": 1}]
        )
        allocation_ids = list(
            QualityShipmentOrderAllocation.objects.filter(
                shipment_line__batch=batch
            ).values_list("id", flat=True)
        )
        self.assertEqual(
            delivered_quantities_by_order([self.order.pk])[self.order.pk], 1_000
        )

        response = self.client.post(
            f"{self.endpoint}{batch.pk}/void-confirmed/",
            {"void_reason": "整张登记有误"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        batch.refresh_from_db()
        self.assertEqual(batch.status, QualityShipmentBatch.Status.VOID)
        self.assertEqual(
            delivered_quantities_by_order([self.order.pk])[self.order.pk], 0
        )
        self.assertFalse(batch.process_card_bindings.exists())
        self.assertEqual(
            list(
                QualityShipmentOrderAllocation.objects.filter(
                    shipment_line__batch=batch
                ).values_list("id", flat=True)
            ),
            allocation_ids,
        )
        self.assertEqual(
            ProcessCard.objects.get(card_no="CARD-VOID-CONFIRMED").status,
            ProcessCard.Status.OPEN,
        )
        self.assertEqual(batch.revisions.count(), 1)

        retried = self.client.post(
            f"{self.endpoint}{batch.pk}/void-confirmed/",
            {"void_reason": "网络重试"},
            format="json",
        )
        self.assertEqual(retried.status_code, 200, retried.content)
        self.assertEqual(batch.revisions.count(), 1)
