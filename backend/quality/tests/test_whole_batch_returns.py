from decimal import Decimal
from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from quality.models import (
    QualityOrder,
    QualityReworkCase,
    QualityReturnAllocation,
    QualityShipmentBatch,
    QualityShipmentLine,
    ReturnRework,
)
from quality.services import delivered_quantities_by_order
from quality.serializers import QualityReworkCaseSerializer

from .helpers import QualityTestMixin, response_results


class WholeBatchCustomerReturnTests(QualityTestMixin, TestCase):
    endpoint = "/api/quality/shipment-batches/"
    candidate_endpoint = "/api/quality/rework-cases/returnable-batches/"

    def create_confirmed_repeat(self, *, shipment_no="QS-RETURN-34", count=34):
        self.order.order_quantity = 50_000
        self.order.save(update_fields=["order_quantity", "updated_at"])
        draft = self.client.post(
            self.endpoint,
            {
                "shipment_no": shipment_no,
                "order_id": self.order.pk,
                "specification_snapshot": self.order.specification,
                "material_snapshot": self.order.material,
                "unit_weight_g": "8.74230",
                "single_batch_net_weight_kg": "10.200",
                "process_card_shipment_quantity": 1_091,
                "product_batch_count": count,
                "inspector_ids": [self.inspector.pk],
                "lines": [{"order_id": self.order.pk}],
            },
            format="json",
        )
        self.assertEqual(draft.status_code, 201, draft.content)
        confirmed = self.client.post(
            f"{self.endpoint}{draft.json()['id']}/confirm/", {}, format="json"
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        return QualityShipmentBatch.objects.get(pk=draft.json()["id"])

    def create_return(self, batch, unit_no=1):
        return self.client.post(
            "/api/quality/rework-cases/",
            {
                "origin": "CUSTOMER_RETURN",
                "shipment_batch_id": batch.pk,
                "shipment_unit_no": unit_no,
                "reason_category": "APPEARANCE",
                "reason": "客户退回整批",
            },
            format="json",
        )

    def test_candidate_exposes_34_physical_units_and_create_fills_one_batch(self):
        batch = self.create_confirmed_repeat()

        candidates = response_results(self.client.get(self.candidate_endpoint))
        row = next(item for item in candidates if item["shipment_batch_id"] == batch.pk)
        self.assertEqual(row["single_batch_net_weight_kg"], "10.200")
        self.assertEqual(row["pieces_per_batch"], 1_167)
        self.assertEqual(row["total_batches"], 34)
        self.assertEqual(row["available_batches"], 34)
        self.assertEqual(row["available_batch_numbers"], list(range(1, 35)))
        self.assertEqual(row["next_return_no"], 1)

        response = self.create_return(batch, 1)
        self.assertEqual(response.status_code, 201, response.content)
        payload = response.json()
        self.assertEqual(payload["shipment_unit_no"], 1)
        self.assertEqual(payload["affected_quantity"], 1_167)
        self.assertEqual(Decimal(payload["affected_weight_kg"]), Decimal("10.200"))
        self.assertEqual(payload["source"]["shipment_no"], batch.shipment_no)
        self.assertEqual(payload["source"]["order_ids"], [self.order.pk])
        self.assertEqual(payload["source"]["pieces_per_batch"], 1_167)
        self.assertEqual(QualityReturnAllocation.objects.filter(case_id=payload["id"]).count(), 1)

        refreshed = response_results(self.client.get(self.candidate_endpoint))
        row = next(item for item in refreshed if item["shipment_batch_id"] == batch.pk)
        self.assertEqual(row["available_batches"], 33)
        self.assertEqual(row["returned_batches"], 1)
        self.assertEqual(row["next_return_no"], 2)
        self.assertNotIn(1, row["available_batch_numbers"])

    def test_same_physical_unit_cannot_be_returned_twice_but_cancel_releases_it(self):
        batch = self.create_confirmed_repeat(count=2)
        first = self.create_return(batch, 1)
        self.assertEqual(first.status_code, 201, first.content)
        duplicate = self.create_return(batch, 1)
        self.assertEqual(duplicate.status_code, 400, duplicate.content)
        self.assertIn("已经登记退货", str(duplicate.json()))

        cancelled = self.client.patch(
            f"/api/quality/rework-cases/{first.json()['id']}/",
            {"status": "CANCELLED"},
            format="json",
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.content)
        replacement = self.create_return(batch, 1)
        self.assertEqual(replacement.status_code, 201, replacement.content)

    def test_historical_return_without_unit_reserves_lowest_free_slot(self):
        batch = self.create_confirmed_repeat(count=3)
        line = QualityShipmentLine.objects.get(batch=batch)
        historical = QualityReworkCase.objects.create(
            origin=QualityReworkCase.Origin.CUSTOMER_RETURN,
            shipment_line=line,
            affected_quantity=line.pieces_per_batch,
            affected_weight_kg=line.single_batch_net_weight_kg,
            created_by=self.user,
        )
        self.assertEqual(historical.shipment_batch_id, batch.pk)
        self.assertIsNone(historical.shipment_unit_no)

        row = next(
            item
            for item in response_results(self.client.get(self.candidate_endpoint))
            if item["shipment_batch_id"] == batch.pk
        )
        self.assertEqual(row["available_batch_numbers"], [2, 3])
        self.assertEqual(row["returned_batch_numbers"], [1])
        self.assertEqual(row["next_return_no"], 2)

    def test_historical_two_batch_return_reserves_two_physical_slots(self):
        batch = self.create_confirmed_repeat(count=4)
        line = QualityShipmentLine.objects.get(batch=batch)
        QualityReworkCase.objects.create(
            origin=QualityReworkCase.Origin.CUSTOMER_RETURN,
            shipment_line=line,
            affected_quantity=line.pieces_per_batch * 2,
            affected_weight_kg=line.single_batch_net_weight_kg * 2,
            created_by=self.user,
        )

        row = next(
            item
            for item in response_results(self.client.get(self.candidate_endpoint))
            if item["shipment_batch_id"] == batch.pk
        )
        self.assertEqual(row["returned_batch_numbers"], [1, 2])
        self.assertEqual(row["available_batch_numbers"], [3, 4])

    def test_two_historical_single_batch_cases_reserve_two_distinct_slots(self):
        batch = self.create_confirmed_repeat(count=3)
        line = QualityShipmentLine.objects.get(batch=batch)
        for _ in range(2):
            QualityReworkCase.objects.create(
                origin=QualityReworkCase.Origin.CUSTOMER_RETURN,
                shipment_line=line,
                affected_quantity=line.pieces_per_batch,
                affected_weight_kg=line.single_batch_net_weight_kg,
                created_by=self.user,
            )

        row = next(
            item
            for item in response_results(self.client.get(self.candidate_endpoint))
            if item["shipment_batch_id"] == batch.pk
        )
        self.assertEqual(row["returned_batch_numbers"], [1, 2])
        self.assertEqual(row["available_batch_numbers"], [3])

    def test_explicit_unit_plus_historical_case_reserve_different_slots(self):
        batch = self.create_confirmed_repeat(count=3)
        explicit = self.create_return(batch, 1)
        self.assertEqual(explicit.status_code, 201, explicit.content)
        line = QualityShipmentLine.objects.get(batch=batch)
        QualityReworkCase.objects.create(
            origin=QualityReworkCase.Origin.CUSTOMER_RETURN,
            shipment_line=line,
            affected_quantity=line.pieces_per_batch,
            affected_weight_kg=line.single_batch_net_weight_kg,
            created_by=self.user,
        )

        row = next(
            item
            for item in response_results(self.client.get(self.candidate_endpoint))
            if item["shipment_batch_id"] == batch.pk
        )
        self.assertEqual(row["returned_batch_numbers"], [1, 2])
        self.assertEqual(row["available_batch_numbers"], [3])

    def test_ambiguous_historical_return_hides_the_whole_source_group(self):
        batch = self.create_confirmed_repeat(count=3)
        line = QualityShipmentLine.objects.get(batch=batch)
        QualityReworkCase.objects.create(
            origin=QualityReworkCase.Origin.CUSTOMER_RETURN,
            shipment_line=line,
            affected_quantity=100,
            affected_weight_kg=Decimal("1.234"),
            created_by=self.user,
        )

        rows = response_results(self.client.get(self.candidate_endpoint))
        self.assertNotIn(batch.pk, [row["shipment_batch_id"] for row in rows])

    def test_r1_r2_r3_each_auto_fill_the_same_whole_batch(self):
        batch = self.create_confirmed_repeat(count=1)
        created = self.create_return(batch, 1)
        self.assertEqual(created.status_code, 201, created.content)
        case_id = created.json()["id"]

        for expected_round in (1, 2, 3):
            response = self.client.post(
                "/api/quality/rework-attempts/",
                {"case_id": case_id, "notes": f"第{expected_round}次返工"},
                format="json",
            )
            self.assertEqual(response.status_code, 201, response.content)
            self.assertEqual(response.json()["attempt_no"], expected_round)
            self.assertEqual(response.json()["input_quantity"], 1_167)
            self.assertEqual(response.json()["reworked_quantity"], 1_167)
            self.assertEqual(
                Decimal(response.json()["input_weight_kg"]), Decimal("10.200")
            )
            self.assertEqual(
                Decimal(response.json()["reworked_weight_kg"]), Decimal("10.200")
            )

    def test_whole_batch_source_and_quantity_are_immutable_before_first_attempt(self):
        batch = self.create_confirmed_repeat(count=2)
        created = self.create_return(batch, 1)
        self.assertEqual(created.status_code, 201, created.content)
        case_id = created.json()["id"]
        immutable_updates = {
            "origin": "INTERNAL",
            "process_card_id": None,
            "shipment_line_id": None,
            "shipment_batch_id": None,
            "shipment_unit_no": 2,
            "affected_quantity": 1,
            "affected_weight_kg": "0.001",
        }
        for field, value in immutable_updates.items():
            response = self.client.patch(
                f"/api/quality/rework-cases/{case_id}/",
                {field: value},
                format="json",
            )
            self.assertEqual(response.status_code, 400, (field, response.content))
            self.assertIn("不能修改", str(response.json()))
        notes = self.client.patch(
            f"/api/quality/rework-cases/{case_id}/",
            {"notes": "允许补充说明"},
            format="json",
        )
        self.assertEqual(notes.status_code, 200, notes.content)

    def test_registered_return_reason_can_be_corrected_without_changing_source_facts(self):
        batch = self.create_confirmed_repeat(count=2)
        created = self.create_return(batch, 2)
        self.assertEqual(created.status_code, 201, created.content)
        original = QualityReworkCase.objects.get(pk=created.json()["id"])
        original_facts = (
            original.origin,
            original.process_card_id,
            original.shipment_line_id,
            original.shipment_batch_id,
            original.shipment_unit_no,
            original.affected_quantity,
            original.affected_weight_kg,
        )

        response = self.client.patch(
            f"/api/quality/rework-cases/{original.pk}/",
            {
                "reason_category": ReturnRework.ReasonCategory.STICKING,
                "reason": " 模具粘皮，客户整批退回 ",
                "notes": " 手机端补充说明 ",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["reason_category"], "STICKING")
        self.assertEqual(response.json()["reason_category_display"], "粘皮")
        self.assertEqual(response.json()["reason"], "模具粘皮，客户整批退回")
        self.assertEqual(response.json()["notes"], "手机端补充说明")
        original.refresh_from_db()
        self.assertEqual(
            (
                original.origin,
                original.process_card_id,
                original.shipment_line_id,
                original.shipment_batch_id,
                original.shipment_unit_no,
                original.affected_quantity,
                original.affected_weight_kg,
            ),
            original_facts,
        )

    def test_historical_customer_return_source_facts_are_immutable_but_reason_is_editable(self):
        batch = self.create_confirmed_repeat(count=1)
        line = QualityShipmentLine.objects.get(batch=batch)
        historical = QualityReworkCase.objects.create(
            origin=QualityReworkCase.Origin.CUSTOMER_RETURN,
            shipment_line=line,
            affected_quantity=line.pieces_per_batch,
            affected_weight_kg=line.single_batch_net_weight_kg,
            reason_category=ReturnRework.ReasonCategory.OTHER,
            created_by=self.user,
        )

        reason = self.client.patch(
            f"/api/quality/rework-cases/{historical.pk}/",
            {"reason_category": "STICKING", "reason": "历史退货原因补录"},
            format="json",
        )
        self.assertEqual(reason.status_code, 200, reason.content)
        blocked = self.client.patch(
            f"/api/quality/rework-cases/{historical.pk}/",
            {"shipment_line_id": None},
            format="json",
        )
        self.assertEqual(blocked.status_code, 400, blocked.content)
        self.assertIn("不能修改", str(blocked.json()))

    def test_cancelled_return_is_retained_as_an_immutable_audit_record(self):
        batch = self.create_confirmed_repeat(count=1)
        created = self.create_return(batch, 1)
        cancelled = self.client.patch(
            f"/api/quality/rework-cases/{created.json()['id']}/",
            {"status": "CANCELLED"},
            format="json",
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.content)

        response = self.client.patch(
            f"/api/quality/rework-cases/{created.json()['id']}/",
            {"reason": "取消后又修改原因"},
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("已取消", str(response.json()))

    def test_whole_batch_attempt_patch_cannot_change_physical_inputs(self):
        batch = self.create_confirmed_repeat(count=1)
        case = self.create_return(batch, 1)
        attempt = self.client.post(
            "/api/quality/rework-attempts/",
            {"case_id": case.json()["id"]},
            format="json",
        )
        self.assertEqual(attempt.status_code, 201, attempt.content)
        attempt_id = attempt.json()["id"]

        for field, value in {
            "input_quantity": 1,
            "reworked_quantity": 1,
            "input_weight_kg": "0.001",
            "reworked_weight_kg": "0.001",
        }.items():
            response = self.client.patch(
                f"/api/quality/rework-attempts/{attempt_id}/",
                {field: value},
                format="json",
            )
            self.assertEqual(response.status_code, 400, (field, response.content))
            self.assertIn("必须等于", str(response.json()))
        notes = self.client.patch(
            f"/api/quality/rework-attempts/{attempt_id}/",
            {"notes": "允许补充本轮说明"},
            format="json",
        )
        self.assertEqual(notes.status_code, 200, notes.content)
        self.assertEqual(notes.json()["input_quantity"], 1_167)
        self.assertEqual(Decimal(notes.json()["input_weight_kg"]), Decimal("10.200"))

    def test_cancelled_or_scrapped_case_rejects_new_round_but_completed_allows_it(self):
        for status, allowed in (
            ("CANCELLED", False),
            ("SCRAPPED", False),
            ("COMPLETED", True),
        ):
            batch = self.create_confirmed_repeat(
                shipment_no=f"QS-CASE-{status}", count=1
            )
            created = self.create_return(batch, 1)
            self.assertEqual(created.status_code, 201, created.content)
            updated = self.client.patch(
                f"/api/quality/rework-cases/{created.json()['id']}/",
                {"status": status},
                format="json",
            )
            self.assertEqual(updated.status_code, 200, updated.content)
            attempt = self.client.post(
                "/api/quality/rework-attempts/",
                {"case_id": created.json()["id"]},
                format="json",
            )
            self.assertEqual(
                attempt.status_code,
                201 if allowed else 400,
                (status, attempt.content),
            )

    def test_one_physical_unit_crossing_auto_allocated_orders_restores_both(self):
        self.order.order_quantity = 500
        self.order.save(update_fields=["order_quantity", "updated_at"])
        second = QualityOrder.objects.create(
            order_no="ORD-QA-SECOND",
            product_name=self.order.product_name,
            specification=self.order.specification,
            material=self.order.material,
            order_quantity=1_500,
            order_date=self.order.order_date,
            created_by=self.user,
        )
        draft = self.client.post(
            self.endpoint,
            {
                "shipment_no": "QS-CROSS-ORDER-RETURN",
                "order_id": self.order.pk,
                "specification_snapshot": self.order.specification,
                "material_snapshot": self.order.material,
                "unit_weight_g": "2.00000",
                "single_batch_net_weight_kg": "2.000",
                "process_card_shipment_quantity": 1_000,
                "product_batch_count": 2,
                "lines": [{"order_id": self.order.pk}],
            },
            format="json",
        )
        self.assertEqual(draft.status_code, 201, draft.content)
        confirmed = self.client.post(
            f"{self.endpoint}{draft.json()['id']}/confirm/", {}, format="json"
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        batch = QualityShipmentBatch.objects.get(pk=draft.json()["id"])
        self.assertEqual(batch.lines.count(), 1)
        self.assertEqual(batch.lines.get().order_allocations.count(), 2)
        self.assertEqual(
            delivered_quantities_by_order([self.order.pk, second.pk]),
            {self.order.pk: 500, second.pk: 1_500},
        )

        returned = self.create_return(batch, 1)
        self.assertEqual(returned.status_code, 201, returned.content)
        allocations = list(
            QualityReturnAllocation.objects.filter(case_id=returned.json()["id"])
            .select_related("shipment_order_allocation__order")
            .order_by("shipment_order_allocation__sequence")
        )
        self.assertEqual(
            [
                (item.shipment_order_allocation.order_id, item.piece_quantity)
                for item in allocations
            ],
            [(self.order.pk, 500), (second.pk, 500)],
        )
        self.assertEqual(
            delivered_quantities_by_order([self.order.pk, second.pk]),
            {self.order.pk: 0, second.pk: 1_000},
        )

    def test_drafts_do_not_appear_and_candidate_search_matches_order(self):
        batch = self.create_confirmed_repeat(shipment_no="QS-SEARCH-RETURN", count=1)
        draft = self.client.post(
            self.endpoint,
            {
                "shipment_no": "QS-DRAFT-NOT-RETURNABLE",
                "order_id": self.order.pk,
                "unit_weight_g": "8.74230",
                "single_batch_net_weight_kg": "10.200",
                "process_card_shipment_quantity": 1_091,
                "product_batch_count": 1,
                "lines": [{"order_id": self.order.pk}],
            },
            format="json",
        )
        self.assertEqual(draft.status_code, 201, draft.content)

        rows = response_results(
            self.client.get(self.candidate_endpoint, {"q": self.order.order_no})
        )
        self.assertEqual([row["shipment_batch_id"] for row in rows], [batch.pk])

    def test_prefetched_case_source_serialization_does_not_refetch_lines_or_cases(self):
        batch = self.create_confirmed_repeat(count=1)
        created = self.create_return(batch, 1)
        self.assertEqual(created.status_code, 201, created.content)
        case = (
            QualityReworkCase.objects.select_related(
                "shipment_batch__inspector", "shipment_line__batch"
            )
            .prefetch_related(
                "attempts",
                "shipment_batch__inspectors",
                "shipment_batch__lines__order",
                "shipment_batch__lines__order_allocations__order",
                "shipment_batch__lines__process_card__order",
                "shipment_batch__rework_cases__attempts",
            )
            .get(pk=created.json()["id"])
        )
        # Source serialization should reuse the hydrated batch graph.  Guard
        # against regressions that silently add one line/case query per row.
        with patch(
            "quality.services.shipment_return_groups",
            wraps=__import__("quality.services", fromlist=["shipment_return_groups"]).shipment_return_groups,
        ) as groups:
            with self.assertNumQueries(0):
                payload = QualityReworkCaseSerializer(case).data
        self.assertEqual(payload["source"]["shipment_no"], batch.shipment_no)
        self.assertEqual(groups.call_count, 1)

    def test_rework_case_api_prefetches_batch_cases_without_per_row_queries(self):
        first_batch = self.create_confirmed_repeat(
            shipment_no="QS-API-PREFETCH-1", count=2
        )
        first = self.create_return(first_batch, 1)
        second = self.create_return(first_batch, 2)
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(second.status_code, 201, second.content)

        with CaptureQueriesContext(connection) as one_batch_queries:
            response = self.client.get(
                "/api/quality/rework-cases/",
                {"origin": "CUSTOMER_RETURN", "page_size": 1000},
            )
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(len(response_results(response)), 2)

        second_batch = self.create_confirmed_repeat(
            shipment_no="QS-API-PREFETCH-2", count=2
        )
        third = self.create_return(second_batch, 1)
        fourth = self.create_return(second_batch, 2)
        self.assertEqual(third.status_code, 201, third.content)
        self.assertEqual(fourth.status_code, 201, fourth.content)

        with CaptureQueriesContext(connection) as two_batch_queries:
            response = self.client.get(
                "/api/quality/rework-cases/",
                {"origin": "CUSTOMER_RETURN", "page_size": 1000},
            )
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(len(response_results(response)), 4)

        # Doubling rows and source batches must not add per-case source
        # lookups; the view prefetch graph keeps the query count constant.
        self.assertEqual(len(two_batch_queries), len(one_batch_queries))
