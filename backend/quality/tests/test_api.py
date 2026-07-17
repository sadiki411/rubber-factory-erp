from datetime import timedelta

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from orders.models import ProductSpecification
from quality.models import (
    QualityEmployee,
    QualityOrder,
    QualityShipment,
    ReturnRework,
)

from .helpers import QualityTestMixin, response_results


class QualityCrudApiTests(QualityTestMixin, TestCase):
    def test_resources_support_create_retrieve_and_patch(self):
        employee = self.client.post(
            "/api/quality/employees/",
            {
                "employee_no": "QA-API-002",
                "name": "王全能",
                "role": QualityEmployee.Role.BOTH,
                "team": "夜班",
            },
            format="json",
        )
        self.assertEqual(employee.status_code, 201, employee.content)
        employee_id = employee.json()["id"]
        employee_updated = self.client.patch(
            f"/api/quality/employees/{employee_id}/",
            {"team": "白班"},
            format="json",
        )
        self.assertEqual(employee_updated.status_code, 200, employee_updated.content)
        self.assertEqual(employee_updated.json()["team"], "白班")

        order = self.client.post(
            "/api/quality/orders/",
            {
                "order_no": "ORD-API-002",
                "product_name": "橡胶垫片",
                "specification": "30x40",
                "material": "EPDM",
                "order_quantity": 500,
                "order_date": timezone.localdate().isoformat(),
            },
            format="json",
        )
        self.assertEqual(order.status_code, 201, order.content)
        order_id = order.json()["id"]
        order_updated = self.client.patch(
            f"/api/quality/orders/{order_id}/",
            {"notes": "加急订单"},
            format="json",
        )
        self.assertEqual(order_updated.status_code, 200, order_updated.content)
        self.assertEqual(order_updated.json()["notes"], "加急订单")

        shipment = self.client.post(
            "/api/quality/shipments/",
            self.shipment_payload(order_id=order_id, inspector_id=employee_id),
            format="json",
        )
        self.assertEqual(shipment.status_code, 201, shipment.content)
        shipment_id = shipment.json()["id"]
        shipment_updated = self.client.patch(
            f"/api/quality/shipments/{shipment_id}/",
            {"notes": "客户自提"},
            format="json",
        )
        self.assertEqual(shipment_updated.status_code, 200, shipment_updated.content)
        self.assertEqual(shipment_updated.json()["notes"], "客户自提")

        shipment_object = QualityShipment.objects.get(pk=shipment_id)
        rework = self.client.post(
            "/api/quality/reworks/",
            self.rework_payload(
                shipment_object,
                responsible_inspector_id=employee_id,
                rework_employee_id=employee_id,
            ),
            format="json",
        )
        self.assertEqual(rework.status_code, 201, rework.content)
        rework_id = rework.json()["id"]
        rework_updated = self.client.patch(
            f"/api/quality/reworks/{rework_id}/",
            {"notes": "已复检"},
            format="json",
        )
        self.assertEqual(rework_updated.status_code, 200, rework_updated.content)
        self.assertEqual(rework_updated.json()["notes"], "已复检")

        for endpoint in (
            f"/api/quality/employees/{employee_id}/",
            f"/api/quality/orders/{order_id}/",
            f"/api/quality/shipments/{shipment_id}/",
            f"/api/quality/reworks/{rework_id}/",
        ):
            retrieved = self.client.get(endpoint)
            self.assertEqual(retrieved.status_code, 200, retrieved.content)

    def test_delete_is_not_allowed_for_quality_business_records(self):
        shipment = self.create_shipment()
        rework = self.create_rework(shipment)
        resources = (
            (f"/api/quality/employees/{self.inspector.pk}/", QualityEmployee, self.inspector.pk),
            (f"/api/quality/orders/{self.order.pk}/", QualityOrder, self.order.pk),
            (f"/api/quality/shipments/{shipment.pk}/", QualityShipment, shipment.pk),
            (f"/api/quality/reworks/{rework.pk}/", ReturnRework, rework.pk),
        )

        for endpoint, model, object_id in resources:
            response = self.client.delete(endpoint)
            self.assertEqual(response.status_code, 405, response.content)
            self.assertTrue(model.objects.filter(pk=object_id).exists())

    def test_cumulative_returns_cannot_exceed_shipped_quantity(self):
        shipment = self.create_shipment(
            inspection_quantity=100,
            qualified_quantity=100,
            defective_quantity=0,
            shipped_quantity=100,
        )
        first = self.client.post(
            "/api/quality/reworks/",
            self.rework_payload(
                shipment,
                returned_quantity=60,
                reworked_quantity=50,
                recovered_quantity=45,
                scrap_quantity=5,
            ),
            format="json",
        )
        self.assertEqual(first.status_code, 201, first.content)

        exceeded = self.client.post(
            "/api/quality/reworks/",
            self.rework_payload(
                shipment,
                returned_quantity=41,
                reworked_quantity=0,
                recovered_quantity=0,
                scrap_quantity=0,
            ),
            format="json",
        )
        self.assertEqual(exceeded.status_code, 400, exceeded.content)
        self.assertEqual(ReturnRework.objects.filter(shipment=shipment).count(), 1)

        remaining = self.client.post(
            "/api/quality/reworks/",
            self.rework_payload(
                shipment,
                returned_quantity=40,
                reworked_quantity=0,
                recovered_quantity=0,
                scrap_quantity=0,
            ),
            format="json",
        )
        self.assertEqual(remaining.status_code, 201, remaining.content)
        self.assertEqual(
            sum(
                ReturnRework.objects.filter(shipment=shipment).values_list(
                    "returned_quantity", flat=True
                )
            ),
            shipment.shipped_quantity,
        )

    def test_nested_order_product_specification_does_not_add_per_row_queries(self):
        product = ProductSpecification.objects.create(
            product_name="品检查询产品",
            specification="QC-QUERY-SPEC",
            material="NBR",
        )
        self.order.product_specification = product
        self.order.save(update_fields=["product_specification", "updated_at"])
        shipments = [self.create_shipment(shipment_no="SHP-QUERY-001")]

        with CaptureQueriesContext(connection) as single_shipment_queries:
            response = self.client.get("/api/quality/shipments/", {"page_size": 100})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            response_results(response)[0]["order"]["product_specification"]["id"],
            product.pk,
        )

        for index in range(2, 6):
            shipments.append(
                self.create_shipment(shipment_no=f"SHP-QUERY-{index:03d}")
            )
        with CaptureQueriesContext(connection) as many_shipment_queries:
            response = self.client.get("/api/quality/shipments/", {"page_size": 100})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertLessEqual(
            len(many_shipment_queries), len(single_shipment_queries) + 1
        )

        self.create_rework(shipments[0])
        with CaptureQueriesContext(connection) as single_rework_queries:
            response = self.client.get("/api/quality/reworks/", {"page_size": 100})
        self.assertEqual(response.status_code, 200, response.content)
        rework_row = response_results(response)[0]
        self.assertEqual(
            rework_row["shipment"]["order"]["product_specification"]["id"],
            product.pk,
        )

        for index, shipment in enumerate(shipments[1:], 2):
            self.create_rework(
                shipment,
                reason=f"查询验证-{index}",
            )
        with CaptureQueriesContext(connection) as many_rework_queries:
            response = self.client.get("/api/quality/reworks/", {"page_size": 100})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertLessEqual(len(many_rework_queries), len(single_rework_queries) + 1)


class QualityFilterApiTests(QualityTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        today = timezone.localdate()
        self.other_employee = QualityEmployee.objects.create(
            employee_no="QC-002",
            name="赵品检",
            role=QualityEmployee.Role.BOTH,
            team="夜班",
        )
        self.other_order = QualityOrder.objects.create(
            order_no="ORD-FILTER-002",
            product_name="橡胶防尘套",
            specification="F-200",
            material="SILICONE",
            order_quantity=300,
            order_date=today,
            created_by=self.user,
        )
        self.first_shipment = self.create_shipment(
            shipment_no="SHP-FILTER-A",
            shipment_date=today - timedelta(days=2),
        )
        self.second_shipment = self.create_shipment(
            shipment_no="SHP-FILTER-B",
            shipment_date=today,
            order=self.other_order,
            inspector=self.other_employee,
        )
        self.first_rework = self.create_rework(
            self.first_shipment,
            rework_date=today - timedelta(days=1),
            reason_category=ReturnRework.ReasonCategory.DIMENSION,
            reason="尺寸偏差-A",
        )
        self.second_rework = self.create_rework(
            self.second_shipment,
            rework_date=today,
            reason_category=ReturnRework.ReasonCategory.MATERIAL,
            reason="材料异常-B",
            responsible_inspector=self.other_employee,
            rework_employee=self.other_employee,
        )

    def assert_list_ids(self, response, expected_ids):
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            {item["id"] for item in response_results(response)}, set(expected_ids)
        )

    def test_employee_and_order_filters(self):
        today = timezone.localdate()
        QualityEmployee.objects.filter(pk=self.reworker.pk).update(is_active=False)

        employee_cases = (
            ({"q": "赵品检"}, {self.other_employee.pk}),
            ({"role": QualityEmployee.Role.REWORKER}, {self.reworker.pk}),
            ({"active": "false"}, {self.reworker.pk}),
        )
        for params, expected in employee_cases:
            with self.subTest(resource="employee", params=params):
                self.assert_list_ids(
                    self.client.get("/api/quality/employees/", params), expected
                )

        order_cases = (
            ({"q": "FILTER-002"}, {self.other_order.pk}),
            (
                {"date_from": today.isoformat(), "date_to": today.isoformat()},
                {self.other_order.pk},
            ),
            (
                {"status": QualityOrder.Status.OPEN},
                {self.order.pk, self.other_order.pk},
            ),
        )
        for params, expected in order_cases:
            with self.subTest(resource="order", params=params):
                self.assert_list_ids(
                    self.client.get("/api/quality/orders/", params), expected
                )

    def test_shipment_filters_support_search_date_employee_and_order(self):
        today = timezone.localdate()
        cases = (
            ({"q": "FILTER-A"}, {self.first_shipment.pk}),
            (
                {
                    "date_from": (today - timedelta(days=2)).isoformat(),
                    "date_to": (today - timedelta(days=2)).isoformat(),
                },
                {self.first_shipment.pk},
            ),
            ({"employee": self.other_employee.pk}, {self.second_shipment.pk}),
            ({"inspector": self.inspector.pk}, {self.first_shipment.pk}),
            ({"order": self.other_order.pk}, {self.second_shipment.pk}),
        )
        for params, expected in cases:
            with self.subTest(params=params):
                self.assert_list_ids(
                    self.client.get("/api/quality/shipments/", params), expected
                )

    def test_rework_filters_cover_both_employee_roles_and_order_through_shipment(self):
        today = timezone.localdate()
        cases = (
            ({"q": "尺寸偏差-A"}, {self.first_rework.pk}),
            ({"status": ReturnRework.Status.PENDING}, {self.first_rework.pk, self.second_rework.pk}),
            (
                {
                    "date_from": (today - timedelta(days=1)).isoformat(),
                    "date_to": (today - timedelta(days=1)).isoformat(),
                },
                {self.first_rework.pk},
            ),
            ({"employee": self.inspector.pk}, {self.first_rework.pk}),
            ({"responsible_inspector": self.other_employee.pk}, {self.second_rework.pk}),
            ({"rework_employee": self.reworker.pk}, {self.first_rework.pk}),
            ({"order": self.other_order.pk}, {self.second_rework.pk}),
        )
        for params, expected in cases:
            with self.subTest(params=params):
                self.assert_list_ids(
                    self.client.get("/api/quality/reworks/", params), expected
                )
