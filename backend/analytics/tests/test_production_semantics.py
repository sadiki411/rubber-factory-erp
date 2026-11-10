from datetime import date, timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from analytics.models import ManualPerformanceEntry
from analytics.services import build_dashboard
from production.models import ProductionDailyLog, ProductionRun
from molds.models import Machine


class ProductionEventSemanticsTests(TestCase):
    def test_missing_recorded_zero_positive_cancelled_and_latest_business_date(self):
        start = date(2026, 11, 1)
        user = get_user_model().objects.create_user(username='semantics')
        machine = Machine.objects.create(code='SEMANTICS', name='隔离测试机台')
        run = ProductionRun.objects.create(order_no='P1', specification='TEST', order_quantity=120, planned_mold_count=12, cavities=10, curing_seconds=60, status='COMPLETED', is_ledger_only=True, created_by=user)
        # Daily logs require positive molds; explicitly saved zero exists in
        # optional performance supplements. Keep both constraints unchanged.
        ProductionDailyLog.objects.create(run=run, production_date=start + timedelta(days=2), produced_mold_count=12, cavities_snapshot=10)
        ProductionDailyLog.objects.create(run=run, production_date=start + timedelta(days=3), produced_mold_count=99, is_cancelled=True)
        ManualPerformanceEntry.objects.create(entry_date=start + timedelta(days=1), entry_type='PRODUCTION', staff_name='零值登记', order_no='ZERO', produced_mold_count=0, production_hours=1, machine=machine, created_by=user)
        ManualPerformanceEntry.objects.create(entry_date=start + timedelta(days=4), entry_type='PRODUCTION', staff_name='手工人员', order_no='LEGACY', produced_mold_count=0, production_hours=1, machine=machine, created_by=user)
        data = build_dashboard(date_from=start, date_to=start + timedelta(days=4))
        self.assertEqual([x['production_record_count'] for x in data['daily_trend']], [0, 1, 1, 0, 1])
        self.assertEqual([x['produced_mold_count'] for x in data['daily_trend']], [0, 0, 12, 0, 0])
        self.assertEqual(data['sources']['production']['total'], 3)
        self.assertEqual(data['latest_production_business_date'], start + timedelta(days=4))
        self.assertTrue(data['generated_at'])
        self.assertEqual(sum(row['production_record_count'] for row in data['order_performance']), 3)

    def test_empty_period_is_successful_missing_not_failure_or_asserted_zero(self):
        data = build_dashboard(date_from=date(2026, 11, 1), date_to=date(2026, 11, 2))
        self.assertEqual(data['sources']['production']['total'], 0)
        self.assertEqual(len(data['daily_trend']), 2)
        self.assertIsNone(data['latest_recorded_business_date'])
        self.assertTrue(all(row['production_record_count'] == 0 for row in data['daily_trend']))
