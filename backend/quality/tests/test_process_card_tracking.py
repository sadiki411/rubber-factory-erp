from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from quality.models import (
    DefectReason,
    ProcessCard,
    ProcessCardUnitBinding,
    QualityOrder,
    QualityReworkCase,
    QualityShipmentBatch,
    QualityShipmentLine,
)
from quality.services import delivered_quantities_by_order, order_delivery_totals

from .helpers import QualityTestMixin, response_results


class ProcessCardTrackingApiTests(QualityTestMixin, TestCase):
    batch_endpoint = "/api/quality/shipment-batches/"
    card_endpoint = "/api/quality/process-cards/"
    return_endpoint = "/api/quality/rework-cases/"

    def create_confirmed_repeat(
        self, *, count=2, shipment_no="QS-CARD-TRACK", order_quantity=20_000
    ):
        self.order.order_quantity = order_quantity
        self.order.save(update_fields=["order_quantity", "updated_at"])
        draft = self.client.post(
            self.batch_endpoint,
            {
                "shipment_no": shipment_no,
                "order_id": self.order.pk,
                "unit_weight_g": "10.00000",
                "single_batch_net_weight_kg": "10.000",
                "process_card_shipment_quantity": 1_000,
                "product_batch_count": count,
                "lines": [{"order_id": self.order.pk}],
            },
            format="json",
        )
        self.assertEqual(draft.status_code, 201, draft.content)
        confirmed = self.client.post(
            f"{self.batch_endpoint}{draft.json()['id']}/confirm/", {}, format="json"
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        return QualityShipmentBatch.objects.get(pk=draft.json()["id"])

    def bind(self, batch, cards):
        return self.client.post(
            f"{self.batch_endpoint}{batch.pk}/bind-process-cards/",
            {"cards": cards},
            format="json",
        )

    def scan_return(self, card_no, **values):
        payload = {"card_no": card_no, "reason": "客户整批退回", **values}
        return self.client.post(
            f"{self.return_endpoint}scan-return/", payload, format="json"
        )

    def test_partial_binding_keeps_other_physical_units_unbound(self):
        batch = self.create_confirmed_repeat(count=3)
        response = self.bind(
            batch,
            [
                {"shipment_unit_no": 1, "card_no": "04-M003-2608210025"},
                {"shipment_unit_no": 3, "card_no": "04-M003-2608210028"},
            ],
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(ProcessCardUnitBinding.objects.filter(shipment_batch=batch).count(), 2)
        self.assertEqual(
            sorted(response.json()["process_card_bindings"], key=lambda item: item["shipment_unit_no"])[0]["card_no"],
            "04-M003-2608210025",
        )
        scan = self.client.get(
            f"{self.card_endpoint}scan/", {"code": "04-M003-2608210025"}
        )
        self.assertEqual(scan.status_code, 200, scan.content)
        self.assertTrue(scan.json()["found"])
        self.assertFalse(scan.json()["binding_required"])
        self.assertEqual(scan.json()["active_card"]["qr_text"], "04-M003-2608210025")
        binding = scan.json()["active_card"]["unit_binding"]
        self.assertEqual(binding["order_id"], self.order.pk)
        self.assertEqual(binding["order_no"], self.order.order_no)
        self.assertEqual(binding["item_no"], self.order.item_no)
        self.assertEqual(binding["specification"], self.order.specification)
        self.assertEqual(binding["material"], self.order.material)

    def test_two_cards_on_same_order_each_start_at_first_return(self):
        batch = self.create_confirmed_repeat(count=2)
        bound = self.bind(
            batch,
            [
                {"shipment_unit_no": 1, "card_no": "CARD-0001"},
                {"shipment_unit_no": 2, "card_no": "CARD-0002"},
            ],
        )
        self.assertEqual(bound.status_code, 200, bound.content)
        first = self.scan_return("CARD-0001")
        second = self.scan_return("CARD-0002")
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(second.status_code, 201, second.content)
        self.assertEqual(first.json()["return_round"], 1)
        self.assertEqual(second.json()["return_round"], 1)
        self.assertEqual(first.json()["return_label"], "第1次退货返工")
        duplicate = self.scan_return("CARD-0001")
        self.assertEqual(duplicate.status_code, 400, duplicate.content)
        self.assertIn("待处理退货", str(duplicate.json()))

    def test_scanned_return_and_cancellation_refresh_active_card_status(self):
        batch = self.create_confirmed_repeat(count=1)
        self.assertEqual(
            self.bind(
                batch,
                [{"shipment_unit_no": 1, "card_no": "CARD-STATUS-SYNC"}],
            ).status_code,
            200,
        )
        card = ProcessCard.objects.get(card_no="CARD-STATUS-SYNC")
        self.assertEqual(card.status, ProcessCard.Status.SHIPPED)

        returned = self.scan_return("CARD-STATUS-SYNC")
        self.assertEqual(returned.status_code, 201, returned.content)
        card.refresh_from_db()
        self.assertEqual(card.status, ProcessCard.Status.OPEN)

        cancelled = self.client.patch(
            f"{self.return_endpoint}{returned.json()['id']}/",
            {"status": QualityReworkCase.Status.CANCELLED},
            format="json",
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.content)
        self.assertFalse(cancelled.json()["is_current_return"])
        card.refresh_from_db()
        self.assertEqual(card.status, ProcessCard.Status.SHIPPED)

    def test_reship_reuses_facts_and_next_return_is_second_round(self):
        batch = self.create_confirmed_repeat(count=1)
        self.assertEqual(
            self.bind(batch, [{"shipment_unit_no": 1, "card_no": "CARD-RESHIP"}]).status_code,
            200,
        )
        first = self.scan_return("CARD-RESHIP")
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(
            delivered_quantities_by_order([self.order.pk])[self.order.pk], 0
        )
        reship = self.client.post(
            f"{self.return_endpoint}{first.json()['id']}/reship/",
            {"inspector_ids": [self.inspector.pk]},
            format="json",
        )
        self.assertEqual(reship.status_code, 201, reship.content)
        self.assertEqual(reship.json()["shipped_quantity"], 1_000)
        self.assertEqual(Decimal(reship.json()["net_weight_kg"]), Decimal("10.000"))
        self.assertEqual(
            delivered_quantities_by_order([self.order.pk])[self.order.pk], 1_000
        )
        first_case = QualityReworkCase.objects.get(pk=first.json()["id"])
        self.assertEqual(first_case.status, QualityReworkCase.Status.RESHIPPED)

        second = self.scan_return("CARD-RESHIP")
        self.assertEqual(second.status_code, 201, second.content)
        self.assertEqual(second.json()["return_round"], 2)
        self.assertEqual(second.json()["return_label"], "第2次退货返工")
        first_case.refresh_from_db()
        self.assertFalse(first_case.is_current_return)
        totals = order_delivery_totals([self.order.pk])[self.order.pk]
        self.assertEqual(totals["gross_shipped_quantity"], 2_000)
        self.assertEqual(totals["returned_quantity"], 2_000)
        self.assertEqual(totals["effective_delivered_quantity"], 0)

    def test_shipment_return_and_reship_keep_order_status_in_sync(self):
        batch = self.create_confirmed_repeat(count=1, order_quantity=1_000)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, QualityOrder.Status.COMPLETED)
        self.assertEqual(
            self.bind(
                batch,
                [{"shipment_unit_no": 1, "card_no": "CARD-ORDER-STATUS"}],
            ).status_code,
            200,
        )

        returned = self.scan_return("CARD-ORDER-STATUS")
        self.assertEqual(returned.status_code, 201, returned.content)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, QualityOrder.Status.OPEN)

        reshipped = self.client.post(
            f"{self.return_endpoint}{returned.json()['id']}/reship/",
            {},
            format="json",
        )
        self.assertEqual(reshipped.status_code, 201, reshipped.content)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, QualityOrder.Status.COMPLETED)

    def test_replacement_inherits_history_and_old_qr_resolves_new_card(self):
        batch = self.create_confirmed_repeat(count=1)
        self.bind(batch, [{"shipment_unit_no": 1, "card_no": "CARD-OLD"}])
        first = self.scan_return("CARD-OLD")
        replacement = self.client.post(
            f"{self.card_endpoint}{ProcessCard.objects.get(card_no='CARD-OLD').pk}/replace/",
            {"new_card_no": "CARD-NEW", "notes": "运输遗失补卡"},
            format="json",
        )
        self.assertEqual(replacement.status_code, 201, replacement.content)
        self.assertEqual(replacement.json()["replaces_card_no"], "CARD-OLD")
        # The batch is currently back for rework, so the active replacement
        # correctly reflects zero net delivered pieces instead of a stale OPEN
        # default accidentally masking the refresh.
        self.assertEqual(replacement.json()["status"], ProcessCard.Status.OPEN)
        timeline = self.client.get(
            f"{self.card_endpoint}{replacement.json()['id']}/timeline/"
        )
        self.assertEqual(timeline.status_code, 200, timeline.content)
        self.assertEqual(
            [event["shipment_no"] for event in timeline.json()["events"] if event["type"] == "shipment"],
            [batch.shipment_no],
        )
        immutable = self.client.patch(
            f"{self.card_endpoint}{replacement.json()['id']}/",
            {"card_no": "CARD-NEW-EDITED"},
            format="json",
        )
        self.assertEqual(immutable.status_code, 400, immutable.content)
        old_scan = self.client.get(f"{self.card_endpoint}scan/", {"code": "CARD-OLD"})
        self.assertTrue(old_scan.json()["was_replaced"])
        self.assertEqual(old_scan.json()["active_card"]["card_no"], "CARD-NEW")
        old_return = self.scan_return("CARD-OLD")
        self.assertEqual(old_return.status_code, 400, old_return.content)
        self.assertIn("CARD-NEW", str(old_return.json()))

        reship = self.client.post(
            f"{self.return_endpoint}{first.json()['id']}/reship/", {}, format="json"
        )
        self.assertEqual(reship.status_code, 201, reship.content)
        full_timeline = self.client.get(
            f"{self.card_endpoint}{replacement.json()['id']}/timeline/"
        )
        self.assertEqual(full_timeline.status_code, 200, full_timeline.content)
        self.assertEqual(
            {
                event["shipment_no"]
                for event in full_timeline.json()["events"]
                if event["type"] == "shipment"
            },
            {batch.shipment_no, reship.json()["shipment_no"]},
        )
        second = self.scan_return("CARD-NEW")
        self.assertEqual(second.status_code, 201, second.content)
        self.assertEqual(second.json()["return_round"], 2)
        self.assertEqual(
            QualityReworkCase.objects.get(pk=first.json()["id"]).process_card.card_no,
            "CARD-OLD",
        )

    def test_first_unknown_scan_requires_explicit_source_then_binds(self):
        batch = self.create_confirmed_repeat(count=1)
        missing = self.scan_return("CARD-FIRST")
        self.assertEqual(missing.status_code, 400, missing.content)
        self.assertIn("选择一次原出货记录", str(missing.json()))
        created = self.scan_return(
            "CARD-FIRST",
            shipment_batch_id=batch.pk,
            shipment_unit_no=1,
            order_id=self.order.pk,
        )
        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(created.json()["process_card_no"], "CARD-FIRST")
        self.assertFalse(created.json()["binding_pending"])

    def test_bulk_scan_is_atomic_and_rejects_repeated_card(self):
        batch = self.create_confirmed_repeat(count=2)
        self.bind(
            batch,
            [
                {"shipment_unit_no": 1, "card_no": "CARD-BULK-1"},
                {"shipment_unit_no": 2, "card_no": "CARD-BULK-2"},
            ],
        )
        response = self.client.post(
            f"{self.return_endpoint}bulk-scan-return/",
            {
                "cards": [
                    {"card_no": "CARD-BULK-1"},
                    {"card_no": "CARD-BULK-1"},
                ],
                "reason": "同批客户退回",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(
            QualityReworkCase.objects.filter(
                process_card__card_no__startswith="CARD-BULK"
            ).count(),
            0,
        )

    def test_reason_tags_multi_inspectors_and_filters(self):
        batch = self.create_confirmed_repeat(count=1)
        self.bind(batch, [{"shipment_unit_no": 1, "card_no": "CARD-FILTER-7788"}])
        primary = DefectReason.objects.get(code="STICKING")
        secondary = DefectReason.objects.get(code="FLASH")
        created = self.scan_return(
            "CARD-FILTER-7788",
            primary_reason_id=primary.pk,
            secondary_reason_ids=[secondary.pk],
            inspector_ids=[self.inspector.pk],
            reason="粘皮并伴随毛边",
        )
        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(created.json()["primary_reason_detail"]["code"], "STICKING")
        self.assertEqual(created.json()["secondary_reason_details"][0]["code"], "FLASH")
        self.assertEqual(created.json()["responsible_inspectors"][0]["id"], self.inspector.pk)
        rows = response_results(
            self.client.get(
                self.return_endpoint,
                {
                    "card_suffix": "7788",
                    "return_round": 1,
                    "current": "true",
                    "status": "WAITING_REWORK",
                    "reason": "STICKING",
                },
            )
        )
        self.assertEqual([row["id"] for row in rows], [created.json()["id"]])

    def test_approximate_or_blank_historical_date_requires_note(self):
        batch = self.create_confirmed_repeat(count=1)
        self.bind(batch, [{"shipment_unit_no": 1, "card_no": "CARD-HISTORY"}])
        invalid = self.scan_return(
            "CARD-HISTORY", opened_on=None, date_is_approximate=True
        )
        self.assertEqual(invalid.status_code, 400, invalid.content)
        valid = self.scan_return(
            "CARD-HISTORY",
            opened_on=None,
            date_is_approximate=True,
            backfill_reason="历史补录，日期不确定",
        )
        self.assertEqual(valid.status_code, 201, valid.content)
        self.assertIsNone(valid.json()["opened_on"])

    def test_preserved_no_card_history_is_explicitly_pending_binding(self):
        batch = self.create_confirmed_repeat(count=1)
        line = QualityShipmentLine.objects.get(batch=batch)
        historical = QualityReworkCase.objects.create(
            origin=QualityReworkCase.Origin.CUSTOMER_RETURN,
            shipment_line=line,
            shipment_unit_no=None,
            affected_quantity=1_000,
            affected_weight_kg=Decimal("10.000"),
            reason="旧系统无流程卡记录",
            created_by=self.user,
        )
        detail = self.client.get(f"{self.return_endpoint}{historical.pk}/")
        self.assertEqual(detail.status_code, 200, detail.content)
        self.assertTrue(detail.json()["binding_pending"])
        blocked = self.client.post(
            f"{self.return_endpoint}{historical.pk}/bind-card/",
            {"card_no": "CARD-NO-GUESS"},
            format="json",
        )
        self.assertEqual(blocked.status_code, 400, blocked.content)
        self.assertIn("不能自动猜测", str(blocked.json()))

    def test_confirm_can_atomically_bind_scanned_cards(self):
        self.order.order_quantity = 5_000
        self.order.save(update_fields=["order_quantity", "updated_at"])
        draft = self.client.post(
            self.batch_endpoint,
            {
                "shipment_no": "QS-CONFIRM-CARDS",
                "order_id": self.order.pk,
                "unit_weight_g": "10.00000",
                "single_batch_net_weight_kg": "10.000",
                "process_card_shipment_quantity": 1_000,
                "product_batch_count": 2,
                "lines": [{"order_id": self.order.pk}],
            },
            format="json",
        )
        confirmed = self.client.post(
            f"{self.batch_endpoint}{draft.json()['id']}/confirm/",
            {
                "process_card_bindings": [
                    {"shipment_unit_no": 1, "card_no": "CARD-CONFIRM-1"},
                    {"shipment_unit_no": 2, "card_no": "CARD-CONFIRM-2"},
                ]
            },
            format="json",
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        self.assertEqual(len(confirmed.json()["process_card_bindings"]), 2)

    def test_different_weight_card_lines_are_one_package_each_and_auto_bound(self):
        self.order.order_quantity = 5_000
        self.order.save(update_fields=["order_quantity", "updated_at"])
        first = ProcessCard.objects.create(
            card_no="CARD-VARIABLE-1",
            order=self.order,
            quantity=1_000,
            unit_weight_g="10.00000",
            created_by=self.user,
        )
        second = ProcessCard.objects.create(
            card_no="CARD-VARIABLE-2",
            order=self.order,
            quantity=1_000,
            unit_weight_g="10.00000",
            created_by=self.user,
        )
        draft = self.client.post(
            self.batch_endpoint,
            {
                "shipment_no": "QS-CARD-VARIABLE-WEIGHT",
                "lines": [
                    {
                        "process_card_id": first.pk,
                        "single_batch_net_weight_kg": "9.800",
                    },
                    {
                        "process_card_id": second.pk,
                        "single_batch_net_weight_kg": "10.200",
                    },
                ],
            },
            format="json",
        )
        self.assertEqual(draft.status_code, 201, draft.content)

        confirmed = self.client.post(
            f"{self.batch_endpoint}{draft.json()['id']}/confirm/",
            {},
            format="json",
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        self.assertEqual(Decimal(confirmed.json()["net_weight_kg"]), Decimal("20.000"))
        bindings = {
            row["card_no"]: (
                row["shipment_unit_no"],
                Decimal(row["net_weight_kg"]),
                row["piece_quantity"],
            )
            for row in confirmed.json()["process_card_bindings"]
        }
        self.assertEqual(
            bindings,
            {
                "CARD-VARIABLE-1": (1, Decimal("9.800"), 980),
                "CARD-VARIABLE-2": (2, Decimal("10.200"), 1_020),
            },
        )

        returned = self.scan_return("CARD-VARIABLE-2")
        self.assertEqual(returned.status_code, 201, returned.content)
        self.assertEqual(returned.json()["shipment_unit_no"], 2)
        self.assertEqual(returned.json()["affected_quantity"], 1_020)
        self.assertEqual(
            Decimal(returned.json()["affected_weight_kg"]), Decimal("10.200")
        )

    def test_duplicate_process_card_lines_are_rejected_before_draft_creation(self):
        card = ProcessCard.objects.create(
            card_no="CARD-DUPLICATE-LINE",
            order=self.order,
            quantity=1_000,
            unit_weight_g="10.00000",
            created_by=self.user,
        )
        response = self.client.post(
            self.batch_endpoint,
            {
                "shipment_no": "QS-DUPLICATE-CARD-LINE",
                "lines": [
                    {"process_card_id": card.pk, "net_weight_kg": "4.000"},
                    {"process_card_id": card.pk, "net_weight_kg": "6.000"},
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("一张流程卡只能对应一包货", str(response.json()))
        self.assertFalse(
            QualityShipmentBatch.objects.filter(
                shipment_no="QS-DUPLICATE-CARD-LINE"
            ).exists()
        )

    def test_duplicate_equal_weight_scan_is_atomic_and_creates_no_bindings(self):
        batch = self.create_confirmed_repeat(
            count=2, shipment_no="QS-DUPLICATE-SCAN"
        )
        response = self.bind(
            batch,
            [
                {"shipment_unit_no": 1, "card_no": "CARD-DUPLICATE-SCAN"},
                {"shipment_unit_no": 2, "card_no": "CARD-DUPLICATE-SCAN"},
            ],
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("本次扫描中重复", str(response.json()))
        self.assertFalse(
            ProcessCardUnitBinding.objects.filter(shipment_batch=batch).exists()
        )

    def test_cross_order_repeat_bindings_infer_each_physical_units_order(self):
        """A selected source order must not be copied onto every scanned card."""

        self.order.order_quantity = 1_000
        self.order.save(update_fields=["order_quantity", "updated_at"])
        second = QualityOrder.objects.create(
            order_no="ORD-CARD-AUTO-SECOND",
            item_no="20",
            product_name=self.order.product_name,
            specification=self.order.specification,
            material=self.order.material,
            order_quantity=2_000,
            order_date=self.order.order_date,
            due_date=self.order.due_date,
            created_by=self.user,
        )
        draft = self.client.post(
            self.batch_endpoint,
            {
                "shipment_no": "QS-CARD-CROSS-ORDER",
                "order_id": self.order.pk,
                "unit_weight_g": "10.00000",
                "single_batch_net_weight_kg": "10.000",
                "process_card_shipment_quantity": 1_000,
                "product_batch_count": 3,
                "lines": [{"order_id": self.order.pk}],
            },
            format="json",
        )
        self.assertEqual(draft.status_code, 201, draft.content)
        confirmed = self.client.post(
            f"{self.batch_endpoint}{draft.json()['id']}/confirm/",
            {
                "process_card_bindings": [
                    {"shipment_unit_no": 1, "card_no": "CARD-CROSS-1"},
                    {"shipment_unit_no": 2, "card_no": "CARD-CROSS-2"},
                    {"shipment_unit_no": 3, "card_no": "CARD-CROSS-3"},
                ]
            },
            format="json",
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        self.assertEqual(
            dict(
                ProcessCard.objects.filter(card_no__startswith="CARD-CROSS-")
                .values_list("card_no", "order_id")
            ),
            {
                "CARD-CROSS-1": self.order.pk,
                "CARD-CROSS-2": second.pk,
                "CARD-CROSS-3": second.pk,
            },
        )
