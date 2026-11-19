"""2026-11-19 narrow fix: original unscanned return -> same-unit reship.

Every row is synthetic. In particular, same specification/order is never used
as evidence that a card belongs to the returned physical unit.
"""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Group
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from quality.models import (ProcessCard, ProcessCardUnitBinding, QualityOrder, QualityReworkCase,
                            QualityReturnAllocation, QualityShipmentBatch, QualityShipmentLine,
                            QualityShipmentOrderAllocation)
from quality.services import delivered_quantities_by_order
from .helpers import QualityTestMixin
from . import test_process_card_tracking as tracking_fixture
from . import test_a_resilience_concurrency as concurrency_fixture


class ManualReturnReshipTests(QualityTestMixin, TestCase):
    batch_endpoint = tracking_fixture.ProcessCardTrackingApiTests.batch_endpoint
    card_endpoint = tracking_fixture.ProcessCardTrackingApiTests.card_endpoint
    return_endpoint = tracking_fixture.ProcessCardTrackingApiTests.return_endpoint
    bind = tracking_fixture.ProcessCardTrackingApiTests.bind
    scan_return = tracking_fixture.ProcessCardTrackingApiTests.scan_return

    def fixture(self, *, bound=True, count=1):
        self.order.order_quantity = 60
        self.order.due_date = timezone.localdate()
        self.order.save()
        target = QualityOrder.objects.create(order_no="SYNTH-SECOND", item_no="02", specification=self.order.specification,
            material=self.order.material, order_quantity=40, due_date=timezone.localdate(), created_by=self.user)
        self.identify_same_customer_product(self.order, target)
        response = self.client.post(self.batch_endpoint, {"order_id": self.order.pk, "unit_weight_g": "1",
            "single_batch_net_weight_kg": ".100", "process_card_shipment_quantity": 100, "product_batch_count": count,
            "inspector_ids": [self.inspector.pk], "lines": [{"order_id": self.order.pk}]}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        bid = response.json()["id"]
        response = self.client.post(f"{self.batch_endpoint}{bid}/confirm/", {}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        batch = QualityShipmentBatch.objects.get(pk=bid)
        if bound:
            response = self.bind(batch, [{"shipment_unit_no": 1, "card_no": "SYNTH-EXACT-UNIT"}])
            self.assertEqual(response.status_code, 200, response.content)
        self.target = target
        return batch

    def manual_return(self, batch, unit=1):
        response = self.client.post(self.return_endpoint, {"origin": "CUSTOMER_RETURN", "shipment_batch_id": batch.pk,
            "shipment_unit_no": unit, "opened_on": timezone.localdate().isoformat(), "reason": "合成整批退货"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        return QualityReworkCase.objects.get(pk=response.json()["id"])

    def context(self, case):
        response = self.client.get(f"{self.return_endpoint}{case.pk}/reship-context/")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def reship(self, case, **extra):
        return self.client.post(f"{self.return_endpoint}{case.pk}/reship/", {"inspector_ids": [self.inspector.pk], **extra}, format="json")

    def assert_net(self, first, second):
        self.assertEqual(delivered_quantities_by_order([self.order.pk, self.target.pk]), {self.order.pk: first, self.target.pk: second})

    def test_unscanned_100_0_100_preserves_one_return_and_original_60_40(self):
        batch = self.fixture()
        self.assert_net(60, 40)
        case = self.manual_return(batch)
        self.assertIsNone(case.process_card_id)
        self.assert_net(0, 0)
        allocations = list(QualityReturnAllocation.objects.filter(case=case).values())
        self.assertTrue(self.context(case)["eligible"])
        case.refresh_from_db()
        self.assertIsNone(case.process_card_id)  # GET must not repair history.
        response = self.reship(case, net_weight_kg=".100", piece_quantity=100)
        self.assertEqual(response.status_code, 201, response.content)
        self.assert_net(60, 40)
        case.refresh_from_db()
        self.assertEqual(case.status, "RESHIPPED")
        self.assertEqual(case.process_card.card_no, "SYNTH-EXACT-UNIT")
        self.assertEqual(case.return_round, 1)
        self.assertEqual(case.reshipment_batch_id, response.json()["id"])
        self.assertEqual(QualityReworkCase.objects.count(), 1)
        self.assertEqual(list(QualityReturnAllocation.objects.filter(case=case).values()), allocations)
        self.assertEqual(QualityShipmentBatch.objects.count(), 2)
        self.assertEqual(dict(QualityShipmentOrderAllocation.objects.filter(shipment_line__batch_id=response.json()["id"]).values_list("order_id", "piece_quantity")), {self.order.pk: 60, self.target.pk: 40})
        retry = self.reship(case, net_weight_kg=".100", piece_quantity=100)
        self.assertEqual(retry.status_code, 201, retry.content)
        self.assertEqual(retry.json()["id"], response.json()["id"])
        self.assertEqual(QualityShipmentBatch.objects.count(), 2)
        self.assertEqual(self.reship(case, piece_quantity=101).status_code, 400)
        self.assert_net(60, 40)

    def test_scanned_path_remains_100_0_100_and_second_manual_round_is_2(self):
        self.fixture()
        response = self.scan_return("SYNTH-EXACT-UNIT")
        self.assertEqual(response.status_code, 201, response.content)
        first = QualityReworkCase.objects.get(pk=response.json()["id"])
        self.assert_net(0, 0)
        response = self.reship(first)
        self.assertEqual(response.status_code, 201, response.content)
        self.assert_net(60, 40)
        second = self.manual_return(QualityShipmentBatch.objects.get(pk=response.json()["id"]))
        self.assert_net(0, 0)
        response = self.reship(second)
        self.assertEqual(response.status_code, 201, response.content)
        second.refresh_from_db()
        self.assertEqual(second.return_round, 2)
        self.assert_net(60, 40)

    def test_other_unit_and_same_spec_card_cannot_fill_missing_identity(self):
        batch = self.fixture(count=2)
        case = self.manual_return(batch, unit=2)
        context = self.context(case)
        self.assertFalse(context["eligible"])
        self.assertEqual(context["state"], "MISSING")
        self.assertEqual(self.reship(case).status_code, 400)
        self.assertEqual(QualityShipmentBatch.objects.count(), 1)

    def test_conflicting_direct_line_and_unit_binding_requires_review(self):
        batch = self.fixture()
        case = self.manual_return(batch)
        other = ProcessCard.objects.create(card_no="SYNTH-CONFLICT", order=self.order, quantity=100, unit_weight_g=1, created_by=self.user)
        QualityShipmentLine.objects.filter(batch=batch).update(process_card=other)
        self.assertEqual(self.context(case)["state"], "MULTIPLE")
        before = list(QualityReturnAllocation.objects.values())
        self.assertEqual(self.reship(case).status_code, 400)
        self.assertEqual(list(QualityReturnAllocation.objects.values()), before)
        self.assertEqual(QualityShipmentBatch.objects.count(), 1)

    def test_cancelled_card_and_missing_unit_do_not_guess(self):
        batch = self.fixture()
        case = self.manual_return(batch)
        ProcessCard.objects.filter(card_no="SYNTH-EXACT-UNIT").update(status="CANCELLED")
        self.assertEqual(self.context(case)["state"], "MISSING")
        self.assertEqual(self.reship(case).status_code, 400)
        QualityReworkCase.objects.filter(pk=case.pk).update(shipment_unit_no=None)
        case.refresh_from_db()
        self.assertFalse(self.context(case)["eligible"])
        self.assertEqual(self.reship(case).status_code, 400)

    def test_replaced_card_uses_audited_current_identity_not_old_code(self):
        self.fixture()
        from quality.services import replace_process_card
        card = ProcessCard.objects.get(card_no="SYNTH-EXACT-UNIT")
        replacement = replace_process_card(card, "SYNTH-REPLACEMENT", created_by=self.user)
        case = self.manual_return(replacement.unit_binding.shipment_batch)
        self.assertEqual(self.context(case)["cards"], [{"id": replacement.pk, "card_no": replacement.card_no}])
        self.assertEqual(self.reship(case).status_code, 201)

    def test_read_only_can_inspect_but_not_reship(self):
        case = self.manual_return(self.fixture())
        self.user.groups.add(Group.objects.get_or_create(name="GPT只读测试")[0])
        self.assertTrue(self.context(case)["eligible"])
        self.assertEqual(self.reship(case).status_code, 403)
        self.assertEqual(QualityShipmentBatch.objects.count(), 1)

    def test_failed_reship_rolls_back_identity_and_never_reverses_again(self):
        case = self.manual_return(self.fixture())
        before = list(QualityReturnAllocation.objects.values())
        with patch("quality.services.sync_order_status_from_delivery", side_effect=ValueError("synthetic rollback")):
            self.assertEqual(self.reship(case).status_code, 400)
        case.refresh_from_db()
        self.assertIsNone(case.process_card_id)
        self.assertIsNone(case.return_round)
        self.assertIsNone(case.reshipment_batch_id)
        self.assertEqual(QualityShipmentBatch.objects.count(), 1)
        self.assertEqual(list(QualityReturnAllocation.objects.values()), before)
        self.assert_net(0, 0)

    def test_direct_card_without_binding_is_reused_only_for_exact_unit(self):
        batch = self.fixture()
        card = ProcessCard.objects.get(card_no="SYNTH-EXACT-UNIT")
        ProcessCardUnitBinding.objects.filter(process_card=card).delete()
        QualityShipmentLine.objects.filter(batch=batch).update(process_card=card)
        case = self.manual_return(batch)
        self.assertTrue(self.context(case)["eligible"])
        self.assertEqual(self.reship(case).status_code, 201)
        self.assertEqual(ProcessCard.objects.count(), 1)
        self.assert_net(60, 40)


class ManualReturnConcurrentReshipTests(TransactionTestCase):
    setUp = concurrency_fixture.QualityConcurrentReturnTests.setUp
    _allocation = concurrency_fixture.QualityConcurrentReturnTests._allocation
    _bind = concurrency_fixture.QualityConcurrentReturnTests._bind
    _submit = concurrency_fixture.QualityConcurrentReturnTests._submit
    _race = concurrency_fixture.QualityConcurrentReturnTests._race

    def test_same_original_return_concurrent_reships_are_idempotent(self):
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(self.user)
        response = client.post('/api/quality/rework-cases/', {
            'origin': 'CUSTOMER_RETURN', 'shipment_batch_id': self.batch.pk,
            'shipment_unit_no': 1, 'reason': '合成并发再发',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        case = QualityReworkCase.objects.get(pk=response.json()['id'])
        before = list(QualityReturnAllocation.objects.filter(case=case).values())
        request = (f'/api/quality/rework-cases/{case.pk}/reship/', {'inspector_ids': [self.employee.pk]})
        statuses = self._race([request, request])
        self.assertIn(201, statuses)
        self.assertTrue(all(status in (201, 503) for status in statuses), statuses)
        retry = client.post(*request, format='json')
        self.assertEqual(retry.status_code, 201, retry.content)
        case.refresh_from_db()
        self.assertEqual(retry.json()['id'], case.reshipment_batch_id)
        self.assertEqual(QualityShipmentBatch.objects.count(), 2)
        self.assertEqual(QualityReworkCase.objects.count(), 1)
        self.assertEqual(list(QualityReturnAllocation.objects.filter(case=case).values()), before)
        self.assertEqual(delivered_quantities_by_order([self.order.pk]), {self.order.pk: 1000})
