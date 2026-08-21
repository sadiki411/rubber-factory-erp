from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from molds.models import MoldModel
from orders.models import ProductSpecification
from quality.models import (
    ProcessCard,
    ProductUnitWeight,
    QualityEmployee,
    QualityOrder,
    QualityShipmentBatch,
    QualityShipmentLine,
)

from .helpers import QualityTestMixin, response_results


class ShipmentLedgerApiTests(QualityTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.due_date = timezone.localdate() + timedelta(days=7)
        self.product = ProductSpecification.objects.create(
            product_name="密封圈A",
            customer_product_no="CP-LEDGER-01",
            specification="20x30",
            material="NBR-70",
        )
        QualityOrder.objects.filter(pk=self.order.pk).update(
            item_no="10",
            product_name=self.product.product_name,
            specification=self.product.specification,
            material=self.product.material,
            product_specification=self.product,
            due_date=self.due_date,
        )
        self.order.refresh_from_db()

    def create_weighted_batch(
        self,
        *,
        status=QualityShipmentBatch.Status.CONFIRMED,
        shipment_no="QS-LEDGER-001",
        shipment_date=None,
        inspector=None,
    ):
        batch = QualityShipmentBatch.objects.create(
            shipment_no=shipment_no,
            shipment_date=shipment_date or timezone.localdate(),
            order=self.order,
            product_specification=self.product,
            product_name_snapshot=self.order.product_name,
            specification_snapshot=self.order.specification,
            material_snapshot=self.order.material,
            inspector=inspector or self.inspector,
            status=QualityShipmentBatch.Status.DRAFT,
            created_by=self.user,
        )
        batch.inspectors.add(inspector or self.inspector)
        QualityShipmentLine.objects.create(
            batch=batch,
            order=self.order,
            product_specification=self.product,
            specification_snapshot=self.order.specification,
            material_snapshot=self.order.material,
            net_weight_kg=Decimal("0.250"),
            unit_weight_g_snapshot=Decimal("2.50000"),
            piece_quantity=100,
        )
        if status != QualityShipmentBatch.Status.DRAFT:
            QualityShipmentBatch.objects.filter(pk=batch.pk).update(status=status)
            batch.refresh_from_db()
        return batch

    def test_unit_weight_and_batch_responses_include_nested_references(self):
        mold = MoldModel.objects.create(code="MM-LEDGER", product_name="密封圈模具")
        weight = ProductUnitWeight.objects.create(
            product_specification=self.product,
            mold_model=mold,
            unit_weight_g=Decimal("2.50000"),
            created_by=self.user,
        )
        unit_response = self.client.get(
            "/api/quality/product-unit-weights/", {"q": "NBR-70"}
        )
        self.assertEqual(unit_response.status_code, 200, unit_response.content)
        units = response_results(unit_response)
        self.assertEqual([item["id"] for item in units], [weight.pk])
        self.assertEqual(units[0]["product_specification"]["product_name"], "密封圈A")
        self.assertEqual(units[0]["product_specification"]["material"], "NBR-70")
        self.assertTrue(units[0]["product_specification"]["is_active"])
        self.assertEqual(units[0]["mold_model"]["code"], "MM-LEDGER")

        batch = self.create_weighted_batch()
        # Historical rows may have only the legacy primary inspector FK.
        batch.inspectors.clear()
        batch_response = self.client.get(
            f"/api/quality/shipment-batches/{batch.pk}/"
        )
        self.assertEqual(batch_response.status_code, 200, batch_response.content)
        payload = batch_response.json()
        self.assertEqual(payload["order"]["order_no"], self.order.order_no)
        self.assertEqual(payload["product_specification"]["material"], "NBR-70")
        self.assertEqual(payload["inspector_ids"], [self.inspector.pk])
        self.assertEqual(payload["inspectors"][0]["name"], self.inspector.name)
        self.assertEqual(payload["lines"][0]["order"]["item_no"], "10")
        self.assertEqual(
            payload["lines"][0]["product_specification"]["product_name"],
            "密封圈A",
        )

    def test_default_ledger_unifies_legacy_and_confirmed_weighted_records(self):
        legacy = self.create_shipment(shipment_no="SHP-LEDGER-OLD")
        self.create_rework(
            shipment=legacy,
            returned_quantity=5,
            reworked_quantity=5,
            recovered_quantity=4,
            scrap_quantity=1,
        )
        confirmed = self.create_weighted_batch(shipment_no="QS-LEDGER-CONFIRMED")
        draft = self.create_weighted_batch(
            status=QualityShipmentBatch.Status.DRAFT,
            shipment_no="QS-LEDGER-DRAFT",
        )
        QualityShipmentBatch.objects.filter(pk=draft.pk).update(shipment_date=None)
        draft.refresh_from_db()
        self.create_weighted_batch(
            status=QualityShipmentBatch.Status.VOID,
            shipment_no="QS-LEDGER-VOID",
        )

        response = self.client.get(
            "/api/quality/shipment-ledger/", {"page_size": 1000}
        )
        self.assertEqual(response.status_code, 200, response.content)
        rows = response_results(response)
        self.assertEqual(
            {row["key"] for row in rows},
            {f"LEGACY:{legacy.pk}", f"WEIGHTED:{confirmed.pk}"},
        )
        weighted = next(row for row in rows if row["source_type"] == "WEIGHTED")
        legacy_row = next(row for row in rows if row["source_type"] == "LEGACY")
        self.assertEqual(legacy_row["inspection_quantity"], 100)
        self.assertEqual(legacy_row["qualified_quantity"], 90)
        self.assertEqual(legacy_row["defective_quantity"], 10)
        self.assertEqual(legacy_row["returned_quantity"], 5)
        self.assertEqual(legacy_row["rework_count"], 1)
        self.assertEqual(weighted["status"], "CONFIRMED")
        self.assertEqual(weighted["order_nos"], [self.order.order_no])
        self.assertEqual(weighted["item_nos"], ["10"])
        self.assertEqual(weighted["product_names"], ["密封圈A"])
        self.assertEqual(weighted["materials"], ["NBR-70"])
        self.assertEqual(weighted["due_dates"], [self.due_date.isoformat()])
        self.assertEqual(weighted["shipped_quantity"], 100)
        self.assertEqual(weighted["net_weight_kg"], "0.250")
        self.assertEqual(weighted["line_count"], 1)
        self.assertEqual(weighted["record"]["id"], confirmed.pk)
        self.assertEqual(weighted["batch"]["lines"][0]["piece_quantity"], 100)
        self.assertIsNone(weighted["shipment"])

        draft_response = self.client.get(
            "/api/quality/shipment-ledger/",
            {"shipment_status": "DRAFT", "page_size": 1000},
        )
        self.assertEqual(draft_response.status_code, 200, draft_response.content)
        self.assertEqual(
            [row["key"] for row in response_results(draft_response)],
            [f"WEIGHTED:{draft.pk}"],
        )
        ranged_draft_response = self.client.get(
            "/api/quality/shipment-ledger/",
            {
                "shipment_status": "DRAFT",
                "date_from": timezone.localdate().isoformat(),
                "date_to": timezone.localdate().isoformat(),
                "page_size": 1000,
            },
        )
        self.assertEqual(
            [row["key"] for row in response_results(ranged_draft_response)],
            [f"WEIGHTED:{draft.pk}"],
        )

    def test_ledger_filters_search_status_due_date_inspector_and_order(self):
        self.create_shipment(shipment_no="SHP-LEDGER-FILTER")
        second = QualityEmployee.objects.create(
            employee_no="QC-LEDGER-2",
            name="王复核",
            role=QualityEmployee.Role.BOTH,
        )
        batch = self.create_weighted_batch(
            shipment_no="QS-LEDGER-FILTER", inspector=second
        )
        batch.inspectors.add(self.inspector)

        cases = [
            ({"q": "密封圈A"}, 2),
            ({"q": "NBR-70"}, 2),
            ({"q": "QC-LEDGER-2"}, 1),
            ({"inspector": second.pk}, 1),
            ({"order": self.order.pk}, 2),
            ({"material": "nbr-70"}, 2),
            ({"order_status": "OPEN"}, 2),
            ({"delivery_status": "PARTIAL"}, 2),
            ({"due_date_from": self.due_date.isoformat()}, 2),
            ({"due_date_to": self.due_date.isoformat()}, 2),
        ]
        for filters, expected_count in cases:
            with self.subTest(filters=filters):
                response = self.client.get(
                    "/api/quality/shipment-ledger/",
                    {**filters, "page_size": 1000},
                )
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(len(response_results(response)), expected_count)

        invalid = self.client.get(
            "/api/quality/shipment-ledger/", {"delivery_status": "UNKNOWN"}
        )
        self.assertEqual(invalid.status_code, 400)

    def test_weighted_batch_filters_cover_product_due_date_order_and_all_inspectors(self):
        second = QualityEmployee.objects.create(
            employee_no="QC-LEDGER-3",
            name="赵品检",
            role=QualityEmployee.Role.INSPECTOR,
        )
        batch = self.create_weighted_batch(shipment_no="QS-BATCH-FILTER")
        batch.inspectors.add(second)
        filters = [
            {"q": "密封圈A"},
            {"q": "NBR-70"},
            {"q": "赵品检"},
            {"inspector": second.pk},
            {"order": self.order.pk},
            {"material": "NBR"},
            {"order_status": "OPEN"},
            {"delivery_status": "PARTIAL"},
            {"due_date_from": self.due_date.isoformat()},
            {"due_date_to": self.due_date.isoformat()},
        ]
        for query in filters:
            with self.subTest(query=query):
                response = self.client.get(
                    "/api/quality/shipment-batches/",
                    {**query, "page_size": 1000},
                )
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(
                    [row["id"] for row in response_results(response)], [batch.pk]
                )

    def test_process_card_only_batch_resolves_order_for_ledger_and_filters(self):
        card = ProcessCard.objects.create(
            card_no="PC-LEDGER-FALLBACK",
            order=self.order,
            product_specification=self.product,
            product_name_snapshot=self.order.product_name,
            specification_snapshot=self.order.specification,
            material_snapshot=self.order.material,
            quantity=100,
            unit_weight_g=Decimal("2.50000"),
            created_by=self.user,
        )
        batch = QualityShipmentBatch.objects.create(
            shipment_no="QS-LEDGER-FALLBACK",
            shipment_date=timezone.localdate(),
            inspector=self.inspector,
            status=QualityShipmentBatch.Status.DRAFT,
            created_by=self.user,
        )
        batch.inspectors.add(self.inspector)
        QualityShipmentLine.objects.create(
            batch=batch,
            process_card=card,
            net_weight_kg=Decimal("0.250"),
            unit_weight_g_snapshot=Decimal("2.50000"),
            piece_quantity=100,
        )
        QualityShipmentBatch.objects.filter(pk=batch.pk).update(
            status=QualityShipmentBatch.Status.CONFIRMED
        )

        ledger = self.client.get(
            "/api/quality/shipment-ledger/",
            {"q": self.order.product_name, "page_size": 1000},
        )
        self.assertEqual(ledger.status_code, 200, ledger.content)
        ledger_row = next(
            row
            for row in response_results(ledger)
            if row["key"] == f"WEIGHTED:{batch.pk}"
        )
        self.assertEqual(ledger_row["order_nos"], [self.order.order_no])
        self.assertEqual(ledger_row["due_dates"], [self.due_date.isoformat()])
        self.assertEqual(ledger_row["record"]["order"]["id"], self.order.pk)

        for filters in (
            {"q": self.order.product_name},
            {"due_date_from": self.due_date.isoformat()},
            {"inspector": self.inspector.pk},
            {"order": self.order.pk},
        ):
            with self.subTest(filters=filters):
                response = self.client.get(
                    "/api/quality/shipment-batches/",
                    {**filters, "page_size": 1000},
                )
                self.assertEqual(response.status_code, 200, response.content)
                self.assertIn(
                    batch.pk,
                    [row["id"] for row in response_results(response)],
                )

    def test_due_date_ordering_uses_earliest_order_in_a_multi_order_batch(self):
        early_order = QualityOrder.objects.create(
            order_no="ORD-EARLY",
            item_no="1",
            product_name="急单产品",
            specification="E",
            material="NBR",
            order_quantity=100,
            order_date=timezone.localdate(),
            due_date=timezone.localdate() + timedelta(days=1),
            created_by=self.user,
        )
        late_order = QualityOrder.objects.create(
            order_no="ORD-LATE",
            item_no="1",
            product_name="普通产品",
            specification="L",
            material="NBR",
            order_quantity=100,
            order_date=timezone.localdate(),
            due_date=timezone.localdate() + timedelta(days=20),
            created_by=self.user,
        )
        multi = QualityShipmentBatch.objects.create(
            shipment_no="QS-MULTI-DUE",
            shipment_date=timezone.localdate(),
            order=late_order,
            status=QualityShipmentBatch.Status.DRAFT,
            created_by=self.user,
        )
        QualityShipmentLine.objects.create(
            batch=multi,
            order=early_order,
            net_weight_kg=Decimal("0.100"),
            unit_weight_g_snapshot=Decimal("1.00000"),
            piece_quantity=100,
        )
        QualityShipmentBatch.objects.filter(pk=multi.pk).update(
            status=QualityShipmentBatch.Status.CONFIRMED
        )
        middle = self.create_weighted_batch(shipment_no="QS-MIDDLE-DUE")

        response = self.client.get(
            "/api/quality/shipment-ledger/",
            {"ordering": "due_date", "page_size": 1000},
        )
        self.assertEqual(response.status_code, 200, response.content)
        weighted_ids = [
            row["source_id"]
            for row in response_results(response)
            if row["source_type"] == "WEIGHTED"
        ]
        self.assertEqual(weighted_ids, [multi.pk, middle.pk])

        batch_response = self.client.get(
            "/api/quality/shipment-batches/",
            {"ordering": "due_date", "page_size": 1000},
        )
        self.assertEqual(batch_response.status_code, 200, batch_response.content)
        self.assertEqual(
            [row["id"] for row in response_results(batch_response)],
            [multi.pk, middle.pk],
        )

    def test_batch_due_date_range_requires_one_linked_order_inside_range(self):
        before = QualityOrder.objects.create(
            order_no="ORD-BEFORE-RANGE",
            item_no="1",
            product_name="区间前产品",
            specification="A",
            material="NBR",
            order_quantity=10,
            order_date=timezone.localdate(),
            due_date=timezone.localdate() + timedelta(days=1),
            created_by=self.user,
        )
        after = QualityOrder.objects.create(
            order_no="ORD-AFTER-RANGE",
            item_no="1",
            product_name="区间后产品",
            specification="B",
            material="NBR",
            order_quantity=10,
            order_date=timezone.localdate(),
            due_date=timezone.localdate() + timedelta(days=20),
            created_by=self.user,
        )
        batch = QualityShipmentBatch.objects.create(
            shipment_no="QS-OUTSIDE-DUE-RANGE",
            shipment_date=timezone.localdate(),
            order=before,
            status=QualityShipmentBatch.Status.DRAFT,
            created_by=self.user,
        )
        QualityShipmentLine.objects.create(
            batch=batch,
            order=after,
            net_weight_kg=Decimal("0.010"),
            unit_weight_g_snapshot=Decimal("1.00000"),
            piece_quantity=10,
        )

        response = self.client.get(
            "/api/quality/shipment-batches/",
            {
                "due_date_from": (timezone.localdate() + timedelta(days=5)).isoformat(),
                "due_date_to": (timezone.localdate() + timedelta(days=10)).isoformat(),
                "page_size": 1000,
            },
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotIn(
            batch.pk,
            [row["id"] for row in response_results(response)],
        )
