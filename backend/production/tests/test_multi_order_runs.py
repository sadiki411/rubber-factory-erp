from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from production.models import (
    ProductionDailyLog,
    ProductionRecordAudit,
    ProductionRun,
    ProductionStation,
)
from quality.models import QualityOrder

from .helpers import ProductionTestMixin


class MultiOrderProductionRunApiTests(ProductionTestMixin, TestCase):
    endpoint = "/api/production/runs/"

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        today = timezone.localdate()
        self.early = self.create_order("COMBINED-EARLY", 700, today + timedelta(days=2))
        self.late = self.create_order("COMBINED-LATE", 500, today + timedelta(days=8))

    def create_order(self, order_no, quantity, due_date, **overrides):
        values = {
            "order_no": order_no,
            "item_no": "10",
            "product_name": "合并生产产品",
            "specification": "20161057",
            "material": "NBR-A",
            "order_quantity": quantity,
            "order_date": timezone.localdate(),
            "due_date": due_date,
            "created_by": self.user,
        }
        values.update(overrides)
        return QualityOrder.objects.create(**values)

    def payload(self, selected_orders=None):
        return {
            "station_id": ProductionStation.objects.get(code="1").pk,
            "status": ProductionRun.Status.PLANNED,
            "order_no": self.early.order_no,
            "specification": self.early.specification,
            "material": self.early.material,
            "order_quantity": 1_200,
            "cavities": 2,
            "estimated_defect_rate": "0.00",
            "planned_mold_count": 600,
            "curing_seconds": 60,
            "estimated_hours": "10.00",
            "selected_orders": selected_orders or [
                {"order_id": self.late.pk, "planned_quantity": 500},
                {"order_id": self.early.pk, "planned_quantity": 700},
            ],
        }

    def test_create_combines_matching_orders_and_distributes_by_due_date(self):
        response = self.client.post(self.endpoint, self.payload(), format="json")
        self.assertEqual(response.status_code, 201, response.content)
        run = ProductionRun.objects.get(pk=response.json()["id"])
        self.assertEqual(run.order_id, self.early.pk)
        self.assertEqual(run.order_quantity, 1_200)
        self.assertEqual(
            [item["order_id"] for item in response.json()["order_allocations"]],
            [self.early.pk, self.late.pk],
        )

        ProductionRun.objects.filter(pk=run.pk).update(
            status=ProductionRun.Status.RUNNING,
            loaded_at=timezone.now(),
        )
        run.refresh_from_db()
        ProductionDailyLog.objects.create(
            run=run,
            production_date=timezone.localdate(),
            operator="张三",
            produced_mold_count=650,
            cavities_snapshot=2,
        )
        run.refresh_from_db()
        distribution = {
            link.order_id: allocated for link, allocated in run.order_distribution()
        }
        self.assertEqual(distribution, {self.early.pk: 700, self.late.pk: 600})

        early = self.client.get(f"/api/orders/orders/{self.early.pk}/")
        late = self.client.get(f"/api/orders/orders/{self.late.pk}/")
        self.assertEqual(early.status_code, 200, early.content)
        self.assertEqual(late.status_code, 200, late.content)
        self.assertEqual(early.json()["produced_quantity"], 700)
        self.assertEqual(late.json()["produced_quantity"], 600)

        board = self.client.get("/api/production/board/")
        self.assertEqual(board.status_code, 200, board.content)
        board_run = next(
            item["run"]
            for group in board.json()["groups"]
            for item in group["stations"]
            if item.get("run", {}).get("id") == run.pk
        )
        self.assertEqual(len(board_run["order_allocations"]), 2)

    def test_rejects_different_material_or_closed_order(self):
        mismatch = self.create_order(
            "COMBINED-MISMATCH",
            100,
            timezone.localdate() + timedelta(days=1),
            material="EPDM",
        )
        response = self.client.post(
            self.endpoint,
            self.payload(
                [
                    {"order_id": self.early.pk, "planned_quantity": 700},
                    {"order_id": mismatch.pk, "planned_quantity": 100},
                ]
            ),
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("规格和材质完全相同", str(response.json()))

        self.late.status = QualityOrder.Status.COMPLETED
        self.late.save(update_fields=["status", "updated_at"])
        response = self.client.post(self.endpoint, self.payload(), format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("已完成或已取消订单不可选择", str(response.json()))

    def test_order_change_requires_reason_and_writes_audit(self):
        created = self.client.post(self.endpoint, self.payload(), format="json")
        self.assertEqual(created.status_code, 201, created.content)
        run_id = created.json()["id"]
        unchanged = self.client.patch(
            f"{self.endpoint}{run_id}/",
            {
                "selected_orders": [
                    {"order_id": self.early.pk, "planned_quantity": 700},
                    {"order_id": self.late.pk, "planned_quantity": 500},
                ],
                "operator": "张三",
            },
            format="json",
        )
        self.assertEqual(unchanged.status_code, 200, unchanged.content)
        self.assertFalse(ProductionRecordAudit.objects.filter(run_id=run_id).exists())

        changed = [
            {"order_id": self.early.pk, "planned_quantity": 650},
            {"order_id": self.late.pk, "planned_quantity": 550},
        ]

        rejected = self.client.patch(
            f"{self.endpoint}{run_id}/",
            {"selected_orders": changed},
            format="json",
        )
        self.assertEqual(rejected.status_code, 400, rejected.content)
        self.assertIn("order_change_reason", rejected.json())

        updated = self.client.patch(
            f"{self.endpoint}{run_id}/",
            {
                "selected_orders": changed,
                "order_change_reason": "现场补货，调整两个订单本次生产数量",
            },
            format="json",
        )
        self.assertEqual(updated.status_code, 200, updated.content)
        audit = ProductionRecordAudit.objects.get(run_id=run_id)
        self.assertEqual(audit.reason, "现场补货，调整两个订单本次生产数量")
        self.assertEqual(audit.before["selected_orders"][0]["planned_quantity"], 700)
        self.assertEqual(audit.after["selected_orders"][0]["planned_quantity"], 650)
