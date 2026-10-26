from decimal import Decimal

from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.utils import timezone

from quality.models import (
    ProcessCard, ProcessCardUnitBinding, QualityReworkCase,
    QualityShipmentBatch, QualityShipmentLine,
)
from quality.read_models import prepare_quality_read_context
from quality.serializers import ProcessCardSerializer, QualityShipmentBatchSerializer

from .helpers import QualityTestMixin


class ScanReadPerformanceTests(QualityTestMixin, TestCase):
    def fixture_card(self, index):
        card = ProcessCard.objects.create(
            card_no=f'SCAN-PERF-{index}', order=self.order, quantity=100,
            unit_weight_g=Decimal('1.00000'), created_by=self.user,
        )
        batch = QualityShipmentBatch.objects.create(
            shipment_no=f'SCAN-PERF-B-{index}', shipment_date=timezone.localdate(),
            inspector=self.inspector, created_by=self.user,
        )
        QualityShipmentLine.objects.create(
            batch=batch, order=self.order, process_card=card,
            net_weight_kg=Decimal('0.100'), piece_quantity=100,
        )
        QualityShipmentBatch.objects.filter(pk=batch.pk).update(status='CONFIRMED')
        return card

    def test_projection_matches_existing_card_calculations_and_does_not_change_rows(self):
        card = self.fixture_card(1)
        expected = ProcessCardSerializer(card, context={}).data
        fresh = ProcessCard.objects.get(pk=card.pk)
        context = {}
        prepare_quality_read_context([fresh], context)
        with self.assertNumQueries(0):
            actual = ProcessCardSerializer(fresh, context=context).data
        self.assertEqual(expected, actual)
        self.assertEqual(actual['shipped_quantity'], 100)
        self.assertEqual(actual['shipped_net_weight_kg'], '0.100')
        self.assertFalse(hasattr(fresh, '_quality_read_totals'))

    def test_query_count_does_not_grow_per_card(self):
        for index in range(1, 21):
            self.fixture_card(index)
        counts = []
        for size in (1, 20):
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get('/api/quality/process-cards/', {'page_size': size})
                self.assertEqual(response.status_code, 200, response.content)
            counts.append(len(queries))
        self.assertLessEqual(counts[1], counts[0] + 2, counts)
        self.assertLessEqual(counts[1], 25, counts)

    def test_request_cache_never_survives_a_new_shipment(self):
        card = self.fixture_card(1)
        first = self.client.get('/api/quality/process-cards/').json()['results'][0]
        batch = QualityShipmentBatch.objects.create(
            shipment_no='SCAN-NEW', shipment_date=timezone.localdate(),
            created_by=self.user,
        )
        QualityShipmentLine.objects.create(
            batch=batch, order=self.order, process_card=card,
            piece_quantity=10, net_weight_kg=Decimal('0.010'),
        )
        QualityShipmentBatch.objects.filter(pk=batch.pk).update(status='CONFIRMED')
        second = self.client.get('/api/quality/process-cards/').json()['results'][0]
        self.assertEqual(first['shipped_quantity'], 100)
        self.assertEqual(second['shipped_quantity'], 110)

    def test_prefetched_batch_totals_preserve_null_piece_quantities(self):
        card = self.fixture_card(1)
        batch = QualityShipmentBatch.objects.prefetch_related('lines').get(lines__process_card=card)
        fields = QualityShipmentBatchSerializer().fields
        with self.assertNumQueries(0):
            self.assertEqual(fields['net_weight_kg'].get_attribute(batch), Decimal('0.100'))
            self.assertEqual(fields['shipped_quantity'].get_attribute(batch), 100)

    def test_legacy_binding_returns_and_replacement_chain_match_without_guessing(self):
        card = self.fixture_card(1)
        source = QualityShipmentBatch.objects.get(lines__process_card=card)
        # A quick-entry package has an aggregate order line, not a card line.
        QualityShipmentLine.objects.filter(batch=source).update(process_card=None)
        ProcessCardUnitBinding.objects.bulk_create([ProcessCardUnitBinding(
            process_card=card, shipment_batch=source, shipment_unit_no=1,
            piece_quantity=100, net_weight_kg=Decimal('0.100'), created_by=self.user,
        )])
        QualityReworkCase.objects.bulk_create([
            QualityReworkCase(case_no='LEGACY-NULL-ROUND', process_card=card,
                shipment_batch=source, shipment_unit_no=1, origin='CUSTOMER_RETURN',
                affected_quantity=20, affected_weight_kg=None, is_current_return=True,
                status='RESHIPPED', created_by=self.user),
            QualityReworkCase(case_no='CURRENT-ROUND', process_card=card,
                shipment_batch=source, shipment_unit_no=1, origin='CUSTOMER_RETURN',
                affected_quantity=10, affected_weight_kg=Decimal('0.010'), return_round=2,
                is_current_return=True, status='WAITING_REWORK', created_by=self.user),
            QualityReworkCase(case_no='IGNORE-CANCELLED', process_card=card,
                origin='CUSTOMER_RETURN', affected_quantity=70, status='CANCELLED',
                created_by=self.user),
            QualityReworkCase(case_no='INTERNAL-ONLY-COUNT', process_card=card,
                origin='INTERNAL', affected_quantity=50, status='OPEN', created_by=self.user),
        ])
        replacement = ProcessCard(card_no='REPLACED-PERF', order=self.order,
            replaces=card, tracking_id=card.tracking_id, quantity=100,
            unit_weight_g=Decimal('1.00000'), created_by=self.user)
        ProcessCard.objects.bulk_create([replacement])
        expected = ProcessCardSerializer(ProcessCard.objects.get(pk=card.pk), context={}).data
        fresh = ProcessCard.objects.get(pk=card.pk)
        context = {}
        prepare_quality_read_context([fresh], context)
        actual = ProcessCardSerializer(fresh, context=context).data
        self.assertEqual(expected, actual)
        self.assertEqual(actual['shipped_quantity'], 100)
        self.assertEqual(actual['returned_piece_quantity'], 30)
        self.assertEqual(actual['delivered_piece_quantity'], 70)
        self.assertEqual(actual['rework_count'], 3)
        self.assertEqual(actual['current_return']['case_no'], 'CURRENT-ROUND')
        self.assertEqual(actual['active_card_no'], 'REPLACED-PERF')
