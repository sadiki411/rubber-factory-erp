from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from quality.models import (
    QualityEmployee,
    QualityOrder,
    QualityShipment,
    ReturnRework,
)


def response_results(response):
    """Return list results regardless of whether DRF pagination is enabled."""
    payload = response.json()
    if isinstance(payload, dict) and "results" in payload:
        return payload["results"]
    return payload


class QualityTestMixin:
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = get_user_model().objects.create_user(
            username="quality-user", password="quality-password"
        )
        cls.inspector = QualityEmployee.objects.create(
            employee_no="QC-001",
            name="张品检",
            role=QualityEmployee.Role.INSPECTOR,
            team="白班",
        )
        cls.reworker = QualityEmployee.objects.create(
            employee_no="RW-001",
            name="李返工",
            role=QualityEmployee.Role.REWORKER,
            team="返工组",
        )
        cls.order = QualityOrder.objects.create(
            order_no="ORD-QA-001",
            product_name="橡胶密封圈",
            specification="20x30",
            material="NBR",
            order_quantity=1000,
            order_date=timezone.localdate() - timedelta(days=3),
            created_by=cls.user,
        )

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def create_shipment(self, **overrides):
        values = {
            "shipment_no": "SHP-QA-001",
            "shipment_date": timezone.localdate(),
            "order": self.order,
            "inspector": self.inspector,
            "inspection_quantity": 100,
            "qualified_quantity": 90,
            "defective_quantity": 10,
            "shipped_quantity": 80,
            "created_by": self.user,
        }
        values.update(overrides)
        return QualityShipment.objects.create(**values)

    def create_rework(self, shipment=None, **overrides):
        values = {
            "shipment": shipment or self.create_shipment(),
            "rework_date": timezone.localdate(),
            "reason_category": ReturnRework.ReasonCategory.APPEARANCE,
            "reason": "外观不良",
            "responsible_inspector": self.inspector,
            "rework_employee": self.reworker,
            "returned_quantity": 10,
            "reworked_quantity": 8,
            "recovered_quantity": 7,
            "scrap_quantity": 1,
            "created_by": self.user,
        }
        values.update(overrides)
        return ReturnRework.objects.create(**values)

    def shipment_payload(self, **overrides):
        payload = {
            "shipment_no": "SHP-API-001",
            "shipment_date": timezone.localdate().isoformat(),
            "order_id": self.order.pk,
            "inspector_id": self.inspector.pk,
            "inspection_quantity": 100,
            "qualified_quantity": 90,
            "defective_quantity": 10,
            "shipped_quantity": 80,
        }
        payload.update(overrides)
        return payload

    def rework_payload(self, shipment, **overrides):
        payload = {
            "shipment_id": shipment.pk,
            "rework_date": timezone.localdate().isoformat(),
            "reason_category": ReturnRework.ReasonCategory.APPEARANCE,
            "reason": "外观不良",
            "responsible_inspector_id": self.inspector.pk,
            "rework_employee_id": self.reworker.pk,
            "returned_quantity": 10,
            "reworked_quantity": 8,
            "recovered_quantity": 7,
            "scrap_quantity": 1,
        }
        payload.update(overrides)
        return payload

