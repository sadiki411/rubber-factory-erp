from datetime import timedelta
from unittest.mock import patch

from django.db import IntegrityError, connection
from django.db.models.query import QuerySet
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from molds.models import MoldModel
from orders.models import ProductSpecification
from quality.models import (
    QualityEmployee,
    QualityOrder,
    QualityShipment,
    ReturnRework,
)
from quality.serializers import QualityEmployeeSerializer

from .helpers import QualityTestMixin, response_results


class QualityCrudApiTests(QualityTestMixin, TestCase):
    def test_employee_quick_create_generates_number_and_defaults_to_inspector(self):
        first = self.client.post(
            "/api/quality/employees/",
            {"name": "快捷品检甲"},
            format="json",
        )
        second = self.client.post(
            "/api/quality/employees/",
            {"name": "快捷品检乙", "employee_no": ""},
            format="json",
        )

        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(second.status_code, 201, second.content)
        self.assertRegex(first.json()["employee_no"], r"^EMP-\d{6}-[0-9A-F]{12}$")
        self.assertRegex(second.json()["employee_no"], r"^EMP-\d{6}-[0-9A-F]{12}$")
        self.assertNotEqual(first.json()["employee_no"], second.json()["employee_no"])
        self.assertEqual(first.json()["role"], QualityEmployee.Role.INSPECTOR)
        self.assertTrue(first.json()["is_active"])

    def test_employee_create_keeps_manual_number_and_normalizes_it(self):
        response = self.client.post(
            "/api/quality/employees/",
            {
                "employee_no": "  qc-manual-01  ",
                "name": "手工编号品检",
                "role": QualityEmployee.Role.BOTH,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["employee_no"], "QC-MANUAL-01")
        self.assertEqual(response.json()["role"], QualityEmployee.Role.BOTH)

    def test_employee_quick_create_rejects_missing_name_and_invalid_role(self):
        missing_name = self.client.post(
            "/api/quality/employees/",
            {},
            format="json",
        )
        invalid_role = self.client.post(
            "/api/quality/employees/",
            {"name": "错误岗位", "role": "UNKNOWN"},
            format="json",
        )

        self.assertEqual(missing_name.status_code, 400, missing_name.content)
        self.assertIn("name", missing_name.json())
        self.assertEqual(invalid_role.status_code, 400, invalid_role.content)
        self.assertIn("role", invalid_role.json())

    def test_employee_updates_omitting_role_preserve_the_existing_role(self):
        full_update = self.client.put(
            f"/api/quality/employees/{self.reworker.pk}/",
            {
                "employee_no": self.reworker.employee_no,
                "name": "李返工（全量更新）",
                "team": self.reworker.team,
                "is_active": True,
                "notes": "",
            },
            format="json",
        )
        self.assertEqual(full_update.status_code, 200, full_update.content)
        self.assertEqual(full_update.json()["role"], QualityEmployee.Role.REWORKER)

        partial_update = self.client.patch(
            f"/api/quality/employees/{self.reworker.pk}/",
            {"name": "李返工（部分更新）"},
            format="json",
        )
        self.assertEqual(partial_update.status_code, 200, partial_update.content)
        self.assertEqual(partial_update.json()["role"], QualityEmployee.Role.REWORKER)

    def test_employee_patch_does_not_overwrite_concurrent_quick_resolve_fields(self):
        employee = QualityEmployee.objects.create(
            employee_no="QC-STALE-PATCH",
            name="并发保留员工",
            role=QualityEmployee.Role.REWORKER,
        )
        serializer = QualityEmployeeSerializer(
            employee,
            data={"team": "夜班"},
            partial=True,
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)

        resolve_key = QualityEmployee.normalize_quick_resolve_key(employee.name)
        QualityEmployee.objects.filter(pk=employee.pk).update(
            role=QualityEmployee.Role.BOTH,
            quick_resolve_key=resolve_key,
        )
        serializer.save()

        employee.refresh_from_db()
        self.assertEqual(employee.team, "夜班")
        self.assertEqual(employee.role, QualityEmployee.Role.BOTH)
        self.assertEqual(employee.quick_resolve_key, resolve_key)

    def test_quick_resolve_rejects_name_whose_normalized_key_is_too_long(self):
        response = self.client.post(
            "/api/quality/employees/quick-resolve/",
            {"name": "\ufdfa" * 100},
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("name", response.json())

    def test_auto_numbered_employee_can_be_used_for_shipment(self):
        employee = self.client.post(
            "/api/quality/employees/",
            {"name": "立即可用品检"},
            format="json",
        )
        self.assertEqual(employee.status_code, 201, employee.content)

        shipment = self.client.post(
            "/api/quality/shipments/",
            self.shipment_payload(inspector_id=employee.json()["id"]),
            format="json",
        )

        self.assertEqual(shipment.status_code, 201, shipment.content)
        self.assertEqual(shipment.json()["inspector"]["id"], employee.json()["id"])

    def test_quick_resolve_employee_reuses_name_and_adds_the_required_role(self):
        existing = QualityEmployee.objects.create(
            employee_no="RW-QUICK-01",
            name="同一位员工",
            role=QualityEmployee.Role.REWORKER,
        )

        response = self.client.post(
            "/api/quality/employees/quick-resolve/",
            {"name": " 同一位员工 ", "purpose": QualityEmployee.Role.INSPECTOR},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["id"], existing.pk)
        self.assertEqual(response.json()["role"], QualityEmployee.Role.BOTH)
        self.assertEqual(QualityEmployee.objects.filter(name="同一位员工").count(), 1)

    def test_quick_resolve_employee_creates_once_even_if_frontend_list_is_empty(self):
        created = self.client.post(
            "/api/quality/employees/quick-resolve/",
            {"name": "服务器解析品检", "purpose": QualityEmployee.Role.INSPECTOR},
            format="json",
        )
        resolved = self.client.post(
            "/api/quality/employees/quick-resolve/",
            {"name": "服务器解析品检", "purpose": QualityEmployee.Role.INSPECTOR},
            format="json",
        )

        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(resolved.status_code, 200, resolved.content)
        self.assertEqual(resolved.json()["id"], created.json()["id"])
        self.assertEqual(QualityEmployee.objects.filter(name="服务器解析品检").count(), 1)

    def test_quick_resolve_normalizes_manual_employee_name_before_claiming_it(self):
        existing = QualityEmployee.objects.create(
            employee_no="QC-NORMALIZED-01",
            name="Ａｌｉｃｅ　品检",
            role=QualityEmployee.Role.INSPECTOR,
        )

        response = self.client.post(
            "/api/quality/employees/quick-resolve/",
            {"name": "alice  品检", "purpose": QualityEmployee.Role.INSPECTOR},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["id"], existing.pk)
        existing.refresh_from_db()
        self.assertEqual(
            existing.quick_resolve_key,
            QualityEmployee.normalize_quick_resolve_key("alice 品检"),
        )

    def test_quick_resolve_recovers_if_manual_claim_loses_unique_key_race(self):
        manual = QualityEmployee.objects.create(
            employee_no="QC-CLAIM-RACE-MANUAL",
            name="认领竞态员工",
            role=QualityEmployee.Role.INSPECTOR,
        )
        resolve_key = QualityEmployee.normalize_quick_resolve_key(manual.name)
        original_update = QuerySet.update
        injected_winner = []

        def race_once(queryset, **kwargs):
            if kwargs.get("quick_resolve_key") == resolve_key and not injected_winner:
                winner = QualityEmployee(
                    employee_no="QC-CLAIM-RACE-WINNER",
                    name=manual.name,
                    role=QualityEmployee.Role.INSPECTOR,
                    quick_resolve_key=resolve_key,
                )
                winner.save(force_insert=True, _skip_unique_validation=True)
                injected_winner.append(winner)
                raise IntegrityError("simulated quick-resolve claim race")
            return original_update(queryset, **kwargs)

        with patch.object(QuerySet, "update", race_once):
            response = self.client.post(
                "/api/quality/employees/quick-resolve/",
                {"name": manual.name, "purpose": QualityEmployee.Role.REWORKER},
                format="json",
            )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["id"], injected_winner[0].pk)
        self.assertEqual(response.json()["role"], QualityEmployee.Role.BOTH)
        manual.refresh_from_db()
        self.assertIsNone(manual.quick_resolve_key)
        self.assertEqual(
            QualityEmployee.objects.filter(quick_resolve_key=resolve_key).count(),
            1,
        )

    def test_quick_resolve_employee_does_not_guess_disabled_or_duplicate_names(self):
        QualityEmployee.objects.create(
            employee_no="QC-DISABLED-01",
            name="停用品检",
            role=QualityEmployee.Role.INSPECTOR,
            is_active=False,
        )
        QualityEmployee.objects.create(
            employee_no="QC-SAME-01",
            name="重名品检",
            role=QualityEmployee.Role.INSPECTOR,
        )
        QualityEmployee.objects.create(
            employee_no="QC-SAME-02",
            name="重名品检",
            role=QualityEmployee.Role.INSPECTOR,
        )

        disabled = self.client.post(
            "/api/quality/employees/quick-resolve/",
            {"name": "停用品检", "purpose": QualityEmployee.Role.INSPECTOR},
            format="json",
        )
        duplicate = self.client.post(
            "/api/quality/employees/quick-resolve/",
            {"name": "重名品检", "purpose": QualityEmployee.Role.INSPECTOR},
            format="json",
        )

        self.assertEqual(disabled.status_code, 400, disabled.content)
        self.assertIn("已停用", str(disabled.json()))
        self.assertEqual(duplicate.status_code, 400, duplicate.content)
        self.assertIn("多名同名", str(duplicate.json()))
    def test_legacy_return_rework_accepts_sticking_reason_category(self):
        shipment = self.create_shipment()
        response = self.client.post(
            "/api/quality/reworks/",
            self.rework_payload(
                shipment,
                reason_category=ReturnRework.ReasonCategory.STICKING,
                reason="产品粘皮",
            ),
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["reason_category"], "STICKING")
        self.assertEqual(response.json()["reason_category_display"], "粘皮")

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
        mold_model = MoldModel.objects.create(
            code="QC-QUERY-MOLD",
            product_name="品检查询模具",
        )
        product = ProductSpecification.objects.create(
            product_name="品检查询产品",
            specification="QC-QUERY-SPEC",
            material="NBR",
            mold_model=mold_model,
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
        self.assertEqual(
            response_results(response)[0]["order"]["product_specification"][
                "mold_model"
            ]["code"],
            mold_model.code,
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
