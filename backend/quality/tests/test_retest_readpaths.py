"""Isolated regression for legacy records beyond the first frontend page."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APITestCase

from quality.models import ProcessCard, QualityOrder, QualityReworkCase


class CompleteReturnReadTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="return-reader")
        cls.order = QualityOrder.objects.create(order_no="READ-OLD", specification="240", material="N7200", order_quantity=1000, created_by=cls.user)
        cls.card = ProcessCard.objects.create(card_no="READ-CARD", order=cls.order, quantity=1000, customer_snapshot="测试客户", created_by=cls.user)
        other_card = ProcessCard.objects.create(card_no="READ-OTHER-CARD", order=cls.order, quantity=1000, created_by=cls.user)
        cls.old_day = timezone.localdate() - timedelta(days=90)
        cls.target = QualityReworkCase.objects.create(case_no="READ-R111", process_card=cls.card, opened_on=cls.old_day, backfill_reason="隔离历史读取样本", affected_quantity=1, status="WAITING_REWORK", return_round=1, created_by=cls.user)
        QualityReworkCase.objects.bulk_create([
            QualityReworkCase(case_no=f"READ-{i}", process_card=cls.card if i < 24 else other_card, opened_on=timezone.localdate(), affected_quantity=1, return_round=i+2, created_by=cls.user)
            for i in range(210)
        ])

    def setUp(self):
        self.client.force_authenticate(self.user)

    def test_search_and_retrieve_find_old_record_beyond_200_without_repair(self):
        url = "/api/quality/rework-cases/"
        response = self.client.get(url, {"q": "READ-R111", "page_size": 20})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["count"], 1)
        self.assertEqual(response.json()["results"][0]["id"], self.target.pk)
        self.assertEqual(self.client.get(url, {"date_from": timezone.localdate().isoformat(), "q": "READ-R111"}).json()["count"], 0)
        with CaptureQueriesContext(connection) as queries:
            detail = self.client.get(f"{url}{self.target.pk}/")
        self.assertEqual(detail.status_code, 200, detail.content)
        self.assertFalse(any(q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for q in queries))
        self.assertEqual(self.client.get(url, {"page_size": 50, "page": 5}).json()["count"], 211)

    def test_tracking_history_is_complete_beyond_one_page_and_ordered(self):
        response = self.client.get(f"/api/quality/process-cards/{self.card.pk}/rework-timeline/")
        self.assertEqual(response.status_code, 200, response.content)
        rows = response.json()
        self.assertEqual(len(rows), 25)
        self.assertEqual(rows[0]["id"], self.target.pk)
        self.assertEqual(len({row["id"] for row in rows}), 25)
        self.assertTrue(all(not row["attempts"] for row in rows))

    def test_server_customer_reason_and_round_filters_compose(self):
        self.target.reason = "拔破"
        self.target.save(update_fields=["reason"])
        response = self.client.get("/api/quality/rework-cases/", {"customer": "测试客户", "reason_text": "拔破", "quick_filter": "ROUND_1"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["count"], 1)
