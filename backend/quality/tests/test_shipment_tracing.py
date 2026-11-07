from django.contrib.auth.models import Group
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from erp.permissions import READ_ONLY_GROUP_NAME
from quality.models import (
    ProcessCard, ProcessCardUnitBinding, QualityOrder, QualityReworkCase,
    QualityShipmentBatch, QualityShipmentBatchRevision, QualityShipmentLine,
)
from quality.tracing import attach_shipment_card_sources, snapshot_card_refs

from .helpers import QualityTestMixin, response_results


class ShipmentTracingTests(QualityTestMixin, TestCase):
    def batch(self, number="QS-TRACE", count=1):
        draft = self.client.post("/api/quality/shipment-batches/", {
            "shipment_no": number, "shipment_date": timezone.localdate().isoformat(),
            "order_id": self.order.pk, "unit_weight_g": "10.00000",
            "single_batch_net_weight_kg": "10.000", "process_card_shipment_quantity": 1000,
            "product_batch_count": count, "inspector_ids": [self.inspector.pk],
            "lines": [{"order_id": self.order.pk}],
        }, format="json")
        self.assertEqual(draft.status_code, 201, draft.content)
        batch_id = draft.json()["id"]
        confirmed = self.client.post(f"/api/quality/shipment-batches/{batch_id}/confirm/", {}, format="json")
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        return QualityShipmentBatch.objects.get(pk=batch_id)

    def bind(self, batch, numbers):
        response = self.client.post(f"/api/quality/shipment-batches/{batch.pk}/bind-process-cards/", {
            "cards": [{"shipment_unit_no": index + 1, "card_no": number} for index, number in enumerate(numbers)],
        }, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        return list(ProcessCard.objects.filter(card_no__in=numbers).order_by("card_no"))

    def trace(self, card):
        response = self.client.get(f"/api/quality/process-cards/{card.pk}/shipment-trace/")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_independent_seven_bindings_are_shown_and_searched_once_without_guessing(self):
        batch = self.batch(count=7)
        numbers = [f"04-M003-26091800{index:02}" for index in range(25, 32)]
        cards = self.bind(batch, numbers)
        unrelated = self.batch("QS-UNRELATED")
        self.assertIsNone(batch.lines.get().process_card_id)
        detail = self.client.get(f"/api/quality/shipment-batches/{batch.pk}/").json()
        self.assertEqual({row["card_no"] for row in detail["card_sources"]}, set(numbers))
        self.assertEqual([row["shipment_unit_no"] for row in detail["card_sources"]], list(range(1, 8)))
        self.assertTrue(all(row["original_paper_order"] is None for row in detail["card_sources"]))
        for query in (numbers[0], "26091800"):
            batches = self.client.get("/api/quality/shipment-batches/", {"q": query})
            self.assertEqual([row["id"] for row in response_results(batches)], [batch.pk])
            ledger = self.client.get("/api/quality/shipment-ledger/", {
                "q": query, "date_from": timezone.localdate().isoformat(), "date_to": timezone.localdate().isoformat(),
            }).json()
            self.assertEqual(ledger["count"], 1)
            self.assertEqual(ledger["results"][0]["source_id"], batch.pk)
        traced = self.trace(cards[0])
        self.assertEqual(traced["shipment_count"], 1)
        self.assertEqual(traced["return_count"], 0)
        self.assertEqual(traced["events"][0]["batch_quantity"], 7000)
        self.assertEqual(traced["events"][0]["card_sources"][0]["piece_quantity"], 1000)
        self.assertNotEqual(traced["events"][0]["id"], unrelated.pk)

    def test_return_reship_and_replacement_keep_first_shipment_and_each_round(self):
        first = self.batch()
        old = self.bind(first, ["TRACE-OLD"])[0]
        returned = self.client.post("/api/quality/rework-cases/scan-return/", {"card_no": old.card_no}, format="json")
        self.assertEqual(returned.status_code, 201, returned.content)
        replace = self.client.post(f"/api/quality/process-cards/{old.pk}/replace/", {"new_card_no": "TRACE-NEW", "notes": "隔离测试补卡"}, format="json")
        self.assertEqual(replace.status_code, 201, replace.content)
        active = ProcessCard.objects.get(pk=replace.json()["id"])
        reship = self.client.post(f"/api/quality/rework-cases/{returned.json()['id']}/reship/", {"inspector_ids": [self.inspector.pk]}, format="json")
        self.assertEqual(reship.status_code, 201, reship.content)
        second = self.client.post("/api/quality/rework-cases/scan-return/", {"card_no": active.card_no}, format="json")
        self.assertEqual(second.status_code, 201, second.content)
        reship2 = self.client.post(f"/api/quality/rework-cases/{second.json()['id']}/reship/", {"inspector_ids": [self.inspector.pk]}, format="json")
        self.assertEqual(reship2.status_code, 201, reship2.content)
        traced = self.trace(active)
        self.assertEqual(traced["shipment_count"], 3)
        self.assertEqual(traced["return_count"], 2)
        self.assertEqual([row["type"] for row in traced["events"]], ["shipment", "return", "reshipment", "return", "reshipment"])
        self.assertEqual(len({row["key"] for row in traced["events"]}), 5)
        first_event = traced["events"][0]
        self.assertEqual(first_event["document_no"], first.shipment_no)
        self.assertEqual(first_event["card_sources"][0]["card_no"], old.card_no)
        self.assertIsNone(first_event["card_sources"][0]["piece_quantity"])
        # Moved binding must never copy the latest package quantity back onto the original shipment.
        self.assertFalse(ProcessCardUnitBinding.objects.filter(shipment_batch=first).exists())
        for number in (old.card_no, active.card_no):
            result = self.client.get("/api/quality/shipment-ledger/", {"q": number}).json()
            self.assertEqual(result["count"], 3)

    def test_revision_only_identity_is_searchable_without_reusing_old_quantity(self):
        batch = self.batch()
        card = ProcessCard.objects.create(card_no="TRACE-REMOVED", order=self.order, quantity=1000, created_by=self.user)
        QualityShipmentBatchRevision.objects.create(
            batch=batch, action="AMEND", reason="隔离测试移除错误标识", operator=self.user,
            before_snapshot={"process_card_bindings": [{"process_card_id": card.pk, "card_no": card.card_no,
                "shipment_unit_no": 1, "piece_quantity": 9999, "net_weight_kg": "99.990"}]},
            after_snapshot={"process_card_bindings": []},
        )
        detail = self.client.get(f"/api/quality/shipment-batches/{batch.pk}/").json()
        source = detail["card_sources"][0]
        self.assertTrue(source["historical_only"])
        self.assertIsNone(source["piece_quantity"])
        trace = self.trace(card)
        self.assertEqual([row["type"] for row in trace["events"]], ["shipment", "correction"])
        self.assertEqual(trace["events"][0]["batch_quantity"], 1000)
        search = self.client.get("/api/quality/shipment-batches/", {"q": card.card_no})
        self.assertEqual([row["id"] for row in response_results(search)], [batch.pk])
        self.assertEqual(list(snapshot_card_refs({"notes": card.card_no, "lines": [None, {"notes": card.card_no}]})), [])

    def test_missing_piece_evidence_is_not_zero_and_legacy_weight_is_explicit(self):
        batch = self.batch()
        card = self.bind(batch, ["TRACE-LEGACY"])[0]
        QualityShipmentLine.objects.filter(batch=batch).update(piece_quantity=None)
        self.assertEqual(self.trace(card)["events"][0]["batch_quantity"], 1000)
        self.assertEqual(self.trace(card)["events"][0]["quantity_evidence"], "按历史单重折算")
        QualityShipmentLine.objects.filter(batch=batch).update(unit_weight_g_snapshot=None)
        self.assertIsNone(self.trace(card)["events"][0]["batch_quantity"])

    def test_readonly_gets_do_not_mutate_business_and_cannot_submit(self):
        batch = self.batch()
        card = self.bind(batch, ["TRACE-READONLY"])[0]
        self.user.groups.add(Group.objects.get_or_create(name=READ_ONLY_GROUP_NAME)[0])
        before = (batch.status, list(batch.lines.values()), list(ProcessCardUnitBinding.objects.values()), QualityReworkCase.objects.count())
        with CaptureQueriesContext(connection) as queries:
            self.trace(card)
            self.client.get(f"/api/quality/shipment-batches/{batch.pk}/")
            self.client.get("/api/quality/shipment-ledger/", {"q": card.card_no})
        self.assertFalse(any(row["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE")) for row in queries))
        batch.refresh_from_db()
        self.assertEqual(before, (batch.status, list(batch.lines.values()), list(ProcessCardUnitBinding.objects.values()), QualityReworkCase.objects.count()))
        self.assertEqual(self.client.post(f"/api/quality/shipment-batches/{batch.pk}/amend/", {}, format="json").status_code, 403)

    def test_bulk_source_queries_do_not_grow_per_batch(self):
        batches = [self.batch(f"QS-BOUNDED-{index}") for index in range(8)]
        for index, batch in enumerate(batches):
            self.bind(batch, [f"TRACE-BOUNDED-{index}"])
        with CaptureQueriesContext(connection) as one:
            attach_shipment_card_sources(list(QualityShipmentBatch.objects.filter(pk=batches[0].pk)))
        with CaptureQueriesContext(connection) as many:
            attach_shipment_card_sources(list(QualityShipmentBatch.objects.all()))
        self.assertEqual(len(one), len(many))

    def test_batch_correction_is_visible_even_when_legacy_snapshot_has_no_card_fields(self):
        batch = self.batch()
        card = self.bind(batch, ["TRACE-WEIGHT-CORRECTION"])[0]
        QualityShipmentBatchRevision.objects.create(batch=batch, action="AMEND", reason="旧格式重量更正", operator=self.user,
            before_snapshot={"net_weight_kg": "11.000"}, after_snapshot={"net_weight_kg": "10.000"})
        event = self.trace(card)["events"][-1]
        self.assertEqual(event["type"], "correction")
        self.assertIn("整张关联出货单", event["scope"])

    def test_binding_only_correction_keeps_saved_shares_and_never_posts_inventory(self):
        target = QualityOrder.objects.create(order_no="TRACE-TARGET", specification=self.order.specification,
            material=self.order.material, order_quantity=1000, order_date=timezone.localdate(), created_by=self.user)
        self.identify_same_customer_product(self.order, target)
        batch = self.batch(count=2)
        old = self.bind(batch, ["TRACE-SOURCE-OLD", "TRACE-SOURCE-KEEP"])[0]
        fields = ("order_id", "piece_quantity", "net_weight_kg", "piece_start", "piece_end")
        before = list(batch.lines.get().order_allocations.values_list(*fields))
        # Today's matching eligibility must not change yesterday's saved split.
        QualityOrder.objects.filter(pk=target.pk).update(source_system="另一个客户")
        with CaptureQueriesContext(connection) as queries:
            amended = self.client.post(f"/api/quality/shipment-batches/{batch.pk}/amend/", {
                "amend_reason": "隔离核验只更正独立包标识", "process_card_bindings": [
                    {"shipment_unit_no": 1, "card_no": "TRACE-SOURCE-NEW"},
                    {"shipment_unit_no": 2, "card_no": "TRACE-SOURCE-KEEP"},
                ],
            }, format="json")
        self.assertEqual(amended.status_code, 200, amended.content)
        self.assertEqual(list(batch.lines.get().order_allocations.values_list(*fields)), before)
        self.assertFalse(any('"inventory_' in row["sql"] and row["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for row in queries))
        self.assertEqual(batch.revisions.count(), 1)
        traced = self.trace(old)
        self.assertEqual([event["type"] for event in traced["events"]], ["shipment", "correction"])
