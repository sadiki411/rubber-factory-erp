from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from orders.models import ProductSpecification
from production.models import ProductionRun, ProductionStation
from quality.models import QualityOrder

from .helpers import ProductionTestMixin, production_workbook_upload


class ProductionOrderLinkageApiTests(ProductionTestMixin, TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.specification_a = ProductSpecification.objects.create(
            product_name="主档产品A",
            specification="主档规格A",
            material="MASTER-A",
        )
        self.specification_b = ProductSpecification.objects.create(
            product_name="主档产品B",
            specification="主档规格B",
            material="MASTER-B",
        )
        self.order_a = self.create_order(
            order_no="MASTER-ORDER-A",
            specification="主档规格A",
            material="MASTER-A",
            product_specification=self.specification_a,
        )
        self.order_b = self.create_order(
            order_no="MASTER-ORDER-B",
            specification="主档规格B",
            material="MASTER-B",
            product_specification=self.specification_b,
        )

    def create_order(self, **overrides):
        values = {
            "order_no": "MASTER-ORDER",
            "product_name": "主档产品",
            "specification": "主档规格",
            "material": "MASTER",
            "order_quantity": 999,
            "order_date": timezone.localdate(),
            "created_by": self.user,
        }
        values.update(overrides)
        return QualityOrder.objects.create(**values)

    def planned_payload(self, **overrides):
        values = {
            "station_id": ProductionStation.objects.get(code="1").pk,
            "order_id": self.order_a.pk,
            "product_specification_id": self.specification_a.pk,
            "order_no": "SNAPSHOT-ORDER-NO",
            "specification": "生产快照规格",
            "material": "SNAPSHOT-MATERIAL",
            "order_quantity": 12,
            "cavities": 2,
            "estimated_defect_rate": "0.00",
            "planned_mold_count": 6,
            "curing_seconds": 60,
            "estimated_hours": "0.10",
            "status": ProductionRun.Status.PLANNED,
        }
        values.update(overrides)
        return values

    def test_create_and_relink_planned_run_without_overwriting_snapshot(self):
        created = self.client.post(
            "/api/production/runs/", self.planned_payload(), format="json"
        )
        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(created.json()["order_id"], self.order_a.pk)
        self.assertEqual(
            created.json()["product_specification_id"], self.specification_a.pk
        )

        run = ProductionRun.objects.get(pk=created.json()["id"])
        self.assertEqual(run.order_no, "SNAPSHOT-ORDER-NO")
        self.assertEqual(run.specification, "生产快照规格")
        self.assertEqual(run.material, "SNAPSHOT-MATERIAL")
        self.assertEqual(run.order_quantity, 12)

        self.order_a.order_no = "MASTER-ORDER-A-CHANGED"
        self.order_a.specification = "主档规格A已修改"
        self.order_a.save()
        self.specification_a.specification = "产品规格主档已修改"
        self.specification_a.save()
        run.refresh_from_db()
        self.assertEqual(run.order_no, "SNAPSHOT-ORDER-NO")
        self.assertEqual(run.specification, "生产快照规格")
        self.assertEqual(run.material, "SNAPSHOT-MATERIAL")

        updated = self.client.patch(
            f"/api/production/runs/{run.pk}/",
            {
                "order_id": self.order_b.pk,
                "product_specification_id": self.specification_b.pk,
            },
            format="json",
        )
        self.assertEqual(updated.status_code, 200, updated.content)
        run.refresh_from_db()
        self.assertEqual(run.order_id, self.order_b.pk)
        self.assertEqual(run.product_specification_id, self.specification_b.pk)
        self.assertEqual(run.order_no, "SNAPSHOT-ORDER-NO")
        self.assertEqual(run.specification, "生产快照规格")

        by_order = self.client.get(
            "/api/production/runs/", {"order_id": self.order_b.pk}
        )
        self.assertEqual(by_order.status_code, 200, by_order.content)
        self.assertEqual(
            [item["id"] for item in by_order.json()["results"]], [run.pk]
        )
        by_specification = self.client.get(
            "/api/production/runs/",
            {"product_specification_id": self.specification_b.pk},
        )
        self.assertEqual(
            by_specification.status_code, 200, by_specification.content
        )
        self.assertEqual(
            [item["id"] for item in by_specification.json()["results"]],
            [run.pk],
        )

    def test_started_or_finished_runs_cannot_change_master_links(self):
        now = timezone.now().replace(microsecond=0)
        mold = self.create_mold(asset_code="ORDER-LINK-RUNNING", machine_code="1")
        runs = [
            ProductionRun.objects.create(
                station=ProductionStation.objects.get(code="1"),
                mold=mold,
                order=self.order_a,
                product_specification=self.specification_a,
                order_no="LOCKED-RUNNING",
                specification="运行中",
                material="SYN-RUBBER-A",
                order_quantity=10,
                cavities=1,
                planned_mold_count=10,
                estimated_hours=1,
                loaded_at=now,
                status=ProductionRun.Status.RUNNING,
                created_by=self.user,
            ),
            ProductionRun.objects.create(
                station=ProductionStation.objects.get(code="2"),
                order=self.order_a,
                product_specification=self.specification_a,
                order_no="LOCKED-COMPLETED",
                specification="已完成",
                material="SYN-RUBBER-A",
                order_quantity=10,
                cavities=1,
                planned_mold_count=10,
                estimated_hours=1,
                loaded_at=now - timedelta(hours=2),
                unloaded_at=now - timedelta(hours=1),
                status=ProductionRun.Status.COMPLETED,
                created_by=self.user,
            ),
            ProductionRun.objects.create(
                station=ProductionStation.objects.get(code="3"),
                order=self.order_a,
                product_specification=self.specification_a,
                order_no="LOCKED-CANCELLED",
                specification="已取消",
                material="SYN-RUBBER-A",
                order_quantity=10,
                cavities=1,
                planned_mold_count=10,
                estimated_hours=1,
                status=ProductionRun.Status.CANCELLED,
                created_by=self.user,
            ),
        ]

        for run in runs:
            with self.subTest(status=run.status):
                order_response = self.client.patch(
                    f"/api/production/runs/{run.pk}/",
                    {"order_id": self.order_b.pk},
                    format="json",
                )
                self.assertEqual(
                    order_response.status_code, 400, order_response.content
                )
                self.assertIn("order_id", order_response.json())
                specification_response = self.client.patch(
                    f"/api/production/runs/{run.pk}/",
                    {"product_specification_id": self.specification_b.pk},
                    format="json",
                )
                self.assertEqual(
                    specification_response.status_code,
                    400,
                    specification_response.content,
                )
                self.assertIn(
                    "product_specification_id", specification_response.json()
                )
                run.refresh_from_db()
                self.assertEqual(run.order_id, self.order_a.pk)
                self.assertEqual(
                    run.product_specification_id, self.specification_a.pk
                )

    def test_master_records_are_protected_while_production_uses_them(self):
        response = self.client.post(
            "/api/production/runs/", self.planned_payload(), format="json"
        )
        self.assertEqual(response.status_code, 201, response.content)

        with self.assertRaises(ProtectedError):
            self.order_a.delete()
        with self.assertRaises(ValidationError):
            self.specification_a.delete()


class ProductionImportOrderLinkageTests(ProductionTestMixin, TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def create_order(self, *, source_key="", product_specification=None):
        return QualityOrder.objects.create(
            order_no="IMPORT-LINK-001",
            product_name="导入关联产品",
            specification="IMPORT-SPEC",
            material="IMPORT-MATERIAL",
            product_specification=product_specification,
            order_quantity=600,
            order_date=timezone.localdate(),
            source_key=source_key,
            created_by=self.user,
        )

    def preview(self):
        return self.client.post(
            "/api/production/imports/preview/",
            {
                "file": production_workbook_upload(
                    [
                        {
                            "station_code": "1",
                            "order_no": "IMPORT-LINK-001",
                            "status": "PLANNED",
                            "specification": "IMPORT-SPEC",
                            "material": "IMPORT-MATERIAL",
                            "order_quantity": 321,
                        }
                    ]
                )
            },
            format="multipart",
        )

    def test_unique_order_match_links_order_and_product_specification(self):
        specification = ProductSpecification.objects.create(
            product_name="导入规格产品",
            specification="IMPORT-SPEC",
            material="IMPORT-MATERIAL",
        )
        order = self.create_order(product_specification=specification)

        preview = self.preview()
        self.assertEqual(preview.status_code, 200, preview.content)
        self.assertEqual(preview.json()["error_count"], 0, preview.json()["issues"])
        row = preview.json()["rows"][0]
        self.assertEqual(row["order_id"], order.pk)
        self.assertEqual(row["product_specification_id"], specification.pk)

        committed = self.client.post(
            "/api/production/imports/commit/",
            {"token": preview.json()["token"]},
            format="json",
        )
        self.assertEqual(committed.status_code, 200, committed.content)
        run = ProductionRun.objects.get(order_no="IMPORT-LINK-001")
        self.assertEqual(run.order_id, order.pk)
        self.assertEqual(run.product_specification_id, specification.pk)
        self.assertEqual(run.specification, "IMPORT-SPEC")
        self.assertEqual(run.material, "IMPORT-MATERIAL")
        self.assertEqual(run.order_quantity, 321)

    def test_missing_or_ambiguous_order_match_warns_and_keeps_links_null(self):
        missing = self.preview()
        self.assertEqual(missing.status_code, 200, missing.content)
        self.assertTrue(
            any("未找到订单号" in issue["message"] for issue in missing.json()["issues"])
        )
        self.assertIsNone(missing.json()["rows"][0]["order_id"])

        QualityOrder.objects.create(
            order_no="IMPORT-LINK-001",
            product_name="重复一",
            specification="IMPORT-SPEC",
            material="IMPORT-MATERIAL",
            order_quantity=600,
            source_key="duplicate-source-1",
            created_by=self.user,
        )
        QualityOrder.objects.create(
            order_no="IMPORT-LINK-001",
            product_name="重复二",
            specification="IMPORT-SPEC",
            material="IMPORT-MATERIAL",
            order_quantity=600,
            source_key="duplicate-source-2",
            created_by=self.user,
        )
        ambiguous = self.preview()
        self.assertEqual(ambiguous.status_code, 200, ambiguous.content)
        self.assertTrue(
            any("匹配到2条" in issue["message"] for issue in ambiguous.json()["issues"])
        )
        self.assertIsNone(ambiguous.json()["rows"][0]["order_id"])
        self.assertIsNone(
            ambiguous.json()["rows"][0]["product_specification_id"]
        )

        committed = self.client.post(
            "/api/production/imports/commit/",
            {"token": ambiguous.json()["token"]},
            format="json",
        )
        self.assertEqual(committed.status_code, 200, committed.content)
        run = ProductionRun.objects.get(order_no="IMPORT-LINK-001")
        self.assertIsNone(run.order_id)
        self.assertIsNone(run.product_specification_id)
