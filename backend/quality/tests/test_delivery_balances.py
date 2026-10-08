from datetime import datetime, timezone as datetime_timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.db.models import Prefetch
from django.utils import timezone

from quality.models import (
    ProcessCard,
    QualityOrder,
    QualityReturnAllocation,
    QualityReworkCase,
    QualityShipmentBatch,
    QualityShipmentLine,
    QualityShipmentOrderAllocation,
    ReturnRework,
)
from quality.services import (
    delivered_quantities_by_order, return_reporting_allocations, shipment_reporting_lines,
)

from .helpers import QualityTestMixin


class DeliveryBalanceTests(QualityTestMixin, TestCase):
    """Cover the scalar query's historical fallbacks without rewriting rows."""

    def line(self, quantity=100, *, batch=None, status="CONFIRMED", **overrides):
        if batch is None:
            batch = QualityShipmentBatch.objects.bulk_create([
                QualityShipmentBatch(
                    shipment_no=f"BALANCE-{QualityShipmentBatch.objects.count()}",
                    status=status,
                    shipment_date=timezone.localdate(),
                    created_by=self.user,
                )
            ])[0]
        values = {
            "batch": batch, "order": self.order, "piece_quantity": quantity,
            "net_weight_kg": Decimal("0.100"),
            "unit_weight_g_snapshot": Decimal("1"),
        }
        values.update(overrides)
        # Raw fixtures intentionally include pre-migration, weight-only rows.
        return QualityShipmentLine.objects.bulk_create([QualityShipmentLine(**values)])[0]

    def share(self, line, quantity=100, *, order=None, sequence=1, start=0):
        return QualityShipmentOrderAllocation.objects.create(
            shipment_line=line, order=order or self.order, sequence=sequence,
            piece_start=start, piece_end=start + quantity, piece_quantity=quantity,
            net_weight_kg=Decimal(quantity) / 1000,
        )

    def case(self, line, quantity=20, **overrides):
        values = {
            "case_no": f"BALANCE-RETURN-{QualityReworkCase.objects.count()}",
            "shipment_line": line, "shipment_batch": line.batch,
            "origin": "CUSTOMER_RETURN", "status": "OPEN",
            "affected_quantity": quantity, "created_by": self.user,
        }
        values.update(overrides)
        return QualityReworkCase.objects.bulk_create([QualityReworkCase(**values)])[0]

    def returned(self, case, line, quantity=20, share=None):
        return QualityReturnAllocation.objects.create(
            case=case, shipment_line=line, shipment_order_allocation=share,
            piece_quantity=quantity, net_weight_kg=Decimal(quantity) / 1000,
        )

    def balance(self):
        return delivered_quantities_by_order([self.order.pk])[self.order.pk]

    def test_empty_input_does_not_query(self):
        with self.assertNumQueries(0):
            self.assertEqual(delivered_quantities_by_order([None]), {})

    def test_legacy_balance_clamps_each_shipment_not_the_order_total(self):
        shipment = self.create_shipment()
        # Restore-like fixture: even an over-return cannot cancel another ship.
        ReturnRework.objects.bulk_create([ReturnRework(
            shipment=shipment, rework_date=timezone.localdate(),
            responsible_inspector=self.inspector, rework_employee=self.reworker,
            returned_quantity=100, created_by=self.user,
        )])
        self.create_shipment(shipment_no="BALANCE-LEGACY-2", shipped_quantity=25)
        self.assertEqual(self.balance(), 25)

    def test_actual_shares_not_source_order_and_each_share_clamps_separately(self):
        other = QualityOrder.objects.create(
            order_no="BALANCE-OTHER", specification=self.order.specification,
            material=self.order.material, order_quantity=1000, created_by=self.user,
        )
        line = self.line(1000)
        first = self.share(line, 600)
        second = self.share(line, 400, order=other, sequence=2, start=600)
        case = self.case(line, 950)
        self.returned(case, line, 850, first)
        self.returned(case, line, 100, second)
        self.share(self.line(50), 50)
        self.assertEqual(
            delivered_quantities_by_order([self.order.pk, other.pk]),
            {self.order.pk: 50, other.pk: 300},
        )
        self.assertEqual(delivered_quantities_by_order([other.pk]), {other.pk: 300})

    def test_single_share_legacy_return_and_direct_case_count_once(self):
        line = self.line()
        self.share(line)
        case = self.case(line)
        self.returned(case, line)  # historical return missing the share FK
        self.case(line, 10)
        self.assertEqual(self.balance(), 70)

    def test_ambiguous_legacy_returns_do_not_guess_between_multiple_shares(self):
        line = self.line()
        self.share(line, 60)
        self.share(line, 40, sequence=2, start=60)
        self.returned(self.case(line), line)
        self.case(line, 10)
        self.assertEqual(self.balance(), 100)

    def test_internal_and_cancelled_returns_do_not_reduce_delivery(self):
        line = self.line()
        share = self.share(line)
        for overrides in ({"origin": "INTERNAL"}, {"status": "CANCELLED"}):
            case = self.case(line, **overrides)
            self.returned(case, line, share=share)
            self.case(line, **overrides)
        self.assertEqual(self.balance(), 100)

    def test_snapshot_weight_half_up_ignores_current_card_weight(self):
        card = ProcessCard.objects.bulk_create([ProcessCard(
            card_no="BALANCE-CARD", order=self.order, quantity=100,
            unit_weight_g=Decimal("50"), created_by=self.user,
        )])[0]
        line = self.line(
            None, process_card=card, order=None, net_weight_kg=Decimal("0.101"),
            unit_weight_g_snapshot=Decimal("2"),
        )
        self.case(line, None, affected_weight_kg=Decimal("0.051"))
        self.assertEqual(self.balance(), 25)  # 50.5 -> 51 minus 25.5 -> 26

    def test_unallocated_old_line_uses_card_unit_when_snapshot_missing(self):
        card = ProcessCard.objects.bulk_create([ProcessCard(
            card_no="BALANCE-FALLBACK", order=self.order, quantity=100,
            unit_weight_g=Decimal("2"), created_by=self.user,
        )])[0]
        line = self.line(
            None, process_card=card, order=None, net_weight_kg=Decimal("0.101"),
            unit_weight_g_snapshot=None,
        )
        case = self.case(line, 26)
        self.returned(case, line, 26)
        self.assertEqual(self.balance(), 25)

    def test_drafts_voids_and_excluded_batch_never_consume_balance(self):
        confirmed = self.line()
        self.share(confirmed)
        self.share(self.line(status="DRAFT"))
        self.share(self.line(status="VOID"))
        self.create_shipment()
        self.assertEqual(self.balance(), 180)
        self.assertEqual(
            delivered_quantities_by_order([self.order.pk], exclude_batch_id=confirmed.batch_id),
            {self.order.pk: 80},
        )

    def test_direct_case_any_allocation_guard_keeps_single_share_history(self):
        line = self.line()
        self.share(line)
        other_line = self.line(batch=line.batch)
        other_share = self.share(other_line)
        case = self.case(line, 30)
        self.returned(case, other_line, 20, other_share)
        self.assertEqual(self.balance(), 180)

    def test_unallocated_direct_case_guard_is_line_specific(self):
        line = self.line()
        other_line = self.line(batch=line.batch)
        self.returned(self.case(line, 30), other_line, 20)
        self.assertEqual(self.balance(), 150)

    def test_query_count_is_fixed_as_weighted_history_grows(self):
        self.share(self.line())
        with self.assertNumQueries(5):
            self.assertEqual(self.balance(), 100)
        for _ in range(49):
            line = self.line()
            share = self.share(line)
            self.returned(self.case(line), line, share=share)
        with self.assertNumQueries(5):
            self.assertEqual(self.balance(), 4020)

    def test_cross_month_business_dates_remain_separate_from_utc_and_entry_dates(self):
        local_zone = ZoneInfo("Asia/Shanghai")
        local_times = [
            datetime(2026, 10, 31, 23, 59, tzinfo=local_zone),
            datetime(2026, 11, 1, 0, 0, tzinfo=local_zone),
        ]
        self.assertEqual(
            local_times[0].astimezone(datetime_timezone.utc).date(),
            local_times[1].astimezone(datetime_timezone.utc).date(),
        )
        lines = []
        for stamp, quantity in zip(local_times, (100, 200)):
            line = self.line(quantity, net_weight_kg=Decimal(quantity) / 1000)
            QualityShipmentBatch.objects.filter(pk=line.batch_id).update(
                shipment_date=stamp.date(), inspector=self.inspector,
                created_at=datetime(2026, 12, 1, tzinfo=datetime_timezone.utc),
            )
            self.share(line, quantity)
            lines.append(line)
        case = self.case(
            lines[0], 100, opened_on=local_times[1].date(),
            responsible_inspector=self.inspector,
        )
        QualityReworkCase.objects.filter(pk=case.pk).update(
            created_at=datetime(2026, 12, 1, tzinfo=datetime_timezone.utc),
        )
        self.returned(case, lines[0], 100, lines[0].order_allocations.get())
        for day, shipped, returned in (("2026-10-31", 100, 0), ("2026-11-01", 200, 100)):
            filters = {"date_from": day, "date_to": day}
            dashboard_response = self.client.get("/api/analytics/dashboard/", filters)
            summary_response = self.client.get("/api/quality/summary/", filters)
            self.assertEqual(dashboard_response.status_code, 200)
            self.assertEqual(summary_response.status_code, 200)
            dashboard = dashboard_response.json()
            for total in (dashboard["quality"]["total"], summary_response.json()["totals"]):
                self.assertEqual(total["shipped_quantity"], shipped)
                self.assertEqual(total["returned_quantity"], returned)
            detail_response = self.client.get(
                "/api/analytics/quality-employee-details/",
                dict(filters, quality_employee_id=self.inspector.pk),
            )
            self.assertEqual(detail_response.status_code, 200)
            products = detail_response.json()["product_stats"]
            self.assertEqual(sum(row["attributed_shipped_quantity"] for row in products), shipped)
            self.assertEqual(sum(row["responsible_return_quantity"] for row in products), returned)
            self.assertEqual(self.balance(), 200)  # lifecycle is not period net

    def test_reporting_loaders_handle_more_than_one_thousand_card_relations(self):
        batch = self.line().batch
        cards = ProcessCard.objects.bulk_create([
            ProcessCard(
                card_no=f"BALANCE-LARGE-{i}", order=self.order,
                quantity=100, unit_weight_g=Decimal("1"), created_by=self.user,
            ) for i in range(1001)
        ])
        lines = QualityShipmentLine.objects.bulk_create([
            QualityShipmentLine(
                batch=batch, process_card=card, order=self.order,
                piece_quantity=100, net_weight_kg=Decimal("0.100"),
                unit_weight_g_snapshot=Decimal("1"),
            ) for card in cards
        ])
        cases = QualityReworkCase.objects.bulk_create([
            QualityReworkCase(
                case_no=f"BALANCE-LARGE-R-{i}", shipment_batch=batch, shipment_line=line,
                affected_quantity=20, origin="CUSTOMER_RETURN", created_by=self.user,
            ) for i, line in enumerate(lines)
        ])
        QualityReturnAllocation.objects.bulk_create([
            QualityReturnAllocation(
                case=case, shipment_line=line, piece_quantity=20,
                net_weight_kg=Decimal("0.020"),
            ) for case, line in zip(cases, lines)
        ])
        with self.assertNumQueries(3):
            loaded_batch = QualityShipmentBatch.objects.prefetch_related(
                Prefetch("lines", queryset=shipment_reporting_lines()),
            ).get(pk=batch.pk)
            loaded_lines = list(loaded_batch.lines.all())
            self.assertEqual(len(loaded_lines), 1002)
            self.assertTrue(all(
                line.process_card.order.pk == self.order.pk
                for line in loaded_lines if line.process_card_id
            ))
        with self.assertNumQueries(2):
            loaded_cases = list(QualityReworkCase.objects.prefetch_related(
                Prefetch("shipment_allocations", queryset=return_reporting_allocations()),
            ))
            self.assertEqual(len(loaded_cases), 1001)
            self.assertTrue(all(
                allocation.shipment_line.process_card.order.pk == self.order.pk
                for case in loaded_cases for allocation in case.shipment_allocations.all()
            ))
