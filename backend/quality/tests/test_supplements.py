"""Isolated acceptance: synthetic goods, employee shipment, explicit manager claims."""
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from analytics.services import build_quality_employee_details
from quality.models import (QualityOrder, QualityReturnAllocation, QualityShipmentBatch, QualityReworkCase,
                            ShipmentSupplementDeliveryAdjustment, ShipmentSupplementEvent)
from quality.services import delivered_quantities_by_order, order_delivery_totals
from orders.overview import build_order_overview
from orders.overview import build_workbench
from .helpers import QualityTestMixin
from . import test_manual_return_reship as existing_returns


@override_settings(QUALITY_SUPPLEMENTS_ENABLED=True)
class SupplementTests(QualityTestMixin, TestCase):
    endpoint = "/api/quality/supplements/"
    batch_endpoint = existing_returns.ManualReturnReshipTests.batch_endpoint
    return_endpoint = existing_returns.ManualReturnReshipTests.return_endpoint
    bind = existing_returns.ManualReturnReshipTests.bind
    manual_return = existing_returns.ManualReturnReshipTests.manual_return
    fixture = existing_returns.ManualReturnReshipTests.fixture

    def setUp(self):
        super().setUp()
        self.manager = APIClient()
        self.manager.force_authenticate(get_user_model().objects.create_user("synth-reconciler", is_staff=True))
        self.identify_same_customer_product(self.order)

    def post(self, route="", *, client=None, expected=201, **data):
        response = (client or self.client).post(self.endpoint + route, {"request_key": str(uuid4()), **data}, format="json")
        self.assertEqual(response.status_code, expected, response.content)
        return response.json()

    def ship(self, *, expected=201, **data):
        base = {"shipment_date": timezone.localdate(), "method": "COUNT", "quantity": 100, "inspector_id": self.inspector.pk}
        if not data.get("issue_id"):
            base.update(kind="REQUEST", product_id=self.order.product_specification_id, reason="合成样本")
        return self.post(expected=expected, **{**base, **data})

    def detail(self, bid):
        result = self.client.get(f"{self.endpoint}{bid}/")
        self.assertEqual(result.status_code, 200, result.content)
        return result.json()

    def claim(self, bid, quantity, *, expected=200, client=None, **data):
        return self.post(f"{bid}/claim/", client=client or self.manager, expected=expected,
                         order_id=self.order.pk, quantity=quantity, note="合成对账凭证", **data)

    def physical_return(self, bid, **overrides):
        return self.post(f"{bid}/return/", expected=200, **{
            "action_date": timezone.localdate(), "note": "合成整包实退", **overrides,
        })

    def net(self):
        return delivered_quantities_by_order([self.order.pk])[self.order.pk]

    def gross(self):
        ledger = self.client.get("/api/quality/shipment-ledger/?compact=1")
        self.assertEqual(ledger.status_code, 200, ledger.content)
        return sum(r["shipped_quantity"] or 0 for r in ledger.json()["results"])

    def test_A_partial_claim_correction_no_claim_and_permission(self):
        bid = self.ship(customer_card_no="SYNTH-A-NEW")["batch_id"]
        self.assertEqual(self.net(), 0)
        self.claim(bid, 60, client=self.client, expected=403)
        claim = self.claim(bid, 60)
        self.assertEqual((self.gross(), self.net(), self.detail(bid)["claimable"]), (100, 60, 40))
        self.claim(bid, 41, expected=400)
        self.ship(customer_card_no="SYNTH-A-NEW", expected=400)
        self.post(f"{bid}/unclaim/", client=self.manager, expected=200, allocation_id=claim["allocation_id"], note="合成更正")
        self.assertEqual((self.gross(), self.net()), (100, 0))
        self.claim(bid, 60)
        self.post(f"{bid}/no-claim/", client=self.manager, expected=200, note="其他40无需挂单")
        self.assertEqual(self.detail(bid)["state"], "NO_CLAIM")
        stats = build_quality_employee_details(date_from=timezone.localdate(), date_to=timezone.localdate(),
                                               quality_employee_id=self.inspector.pk)
        self.assertEqual([(row["quantity"], row["inspection_quantity"]) for row in stats["records"]], [(100, None)])

    def normal_original(self):
        self.order.order_quantity = 100
        self.order.due_date = timezone.localdate()
        self.order.save()
        r = self.client.post(self.batch_endpoint, {"order_id": self.order.pk, "unit_weight_g": "1",
            "single_batch_net_weight_kg": ".100", "process_card_shipment_quantity": 100, "product_batch_count": 1,
            "shipment_date": timezone.localdate() - timedelta(days=2), "backfill_reason": "合成两日前原包",
            "inspector_ids": [self.inspector.pk], "lines": [{"order_id": self.order.pk}]}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        batch = QualityShipmentBatch.objects.get(pk=r.json()["id"])
        r = self.client.post(f"{self.batch_endpoint}{batch.pk}/confirm/", {}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        r = self.bind(batch, [{"shipment_unit_no": 1, "card_no": "SYNTH-B-OLD"}])
        self.assertEqual(r.status_code, 200, r.content)
        batch.refresh_from_db()
        return batch

    def test_B_C_replacement_then_actual_return_and_claim(self):
        base = self.normal_original()
        self.assertEqual(self.net(), 100)
        lookup_url = f"{self.endpoint}origins/?code=SYNTH-B-OLD"
        origin = self.client.get(lookup_url).json()["origins"][0]
        self.assertEqual(origin["batch_id"], base.pk)
        first = self.ship(kind="REPLACEMENT", quantity=60, shipment_date=timezone.localdate() - timedelta(days=1),
                          backfill_reason="合成历史", requested_quantity=100,
                          origins=[{"batch_id": base.pk, "unit_no": 1}])
        self.assertEqual(self.detail(first["batch_id"])["issue"]["remaining"], 40)
        key = str(uuid4())
        data = {"issue_id": first["issue_id"], "expected_packages": 1, "quantity": 40, "request_key": key}
        second = self.ship(**data)
        self.assertTrue(self.ship(**data)["replayed"])
        self.ship(**{**data, "request_key": str(uuid4())}, expected=400)
        self.assertEqual((self.gross(), self.net()), (200, 100))
        details = self.detail(second["batch_id"])["issue"]
        self.assertEqual((details["remaining"], details["retest_note"]), (0, ""))
        self.post(f"issues/{first['issue_id']}/", client=self.manager, expected=200,
                  disputed_quantity=100, dispute_note="合成未退争议", note="不入账")
        self.assertEqual(build_order_overview(self.order.pk, section="shipments")["supplement_disputes"][0]["quantity"], 100)
        self.assertEqual(self.net(), 100)
        self.assertEqual(len(self.client.get(lookup_url).json()["replacements"]), 2)
        self.assertEqual(QualityReworkCase.objects.count(), 0)
        self.manual_return(base)
        self.assertEqual(self.net(), 0)
        self.claim(first["batch_id"], 60, basis="REPLACEMENT_AFTER_RETURN")
        self.claim(second["batch_id"], 40, basis="REPLACEMENT_AFTER_RETURN")
        self.assertEqual((self.gross(), self.net(), QualityReworkCase.objects.count()), (200, 100, 1))

    def released_setup(self):
        original = self.normal_original()
        issue = self.post("issues/", kind="REPLACEMENT", product_id=self.order.product_specification_id,
                          reason="合成硬度超限", requested_quantity=100, origins=[{"batch_id": original.pk, "unit_no": 1}])
        detail = self.manager.get(f"{self.endpoint}issues/{issue['issue_id']}/").json()
        self.assertEqual(len(detail["release_options"]), 1)
        option = detail["release_options"][0]
        self.assertEqual((option["max_quantity"], option["order_id"]), (100, self.order.pk))
        body = {key: option[key] for key in ("origin_id", "allocation_id", "expected_released")}
        body.update(quantity=100, original_delivery_excluded=True, note="合成：翔邦明确原货 100 件不抵原项次")
        return original, issue["issue_id"], body

    def test_reuse_old_number_pending_then_order_reopens_then_new_package_fulfils(self):
        original, issue, body = self.released_setup()
        allocation_snapshot = list(original.lines.values("id", "piece_quantity", "net_weight_kg"))
        self.order.refresh_from_db()
        self.assertEqual((self.net(), self.order.status, self.gross()), (100, "COMPLETED", 100))
        route = f"issues/{issue}/delivery-release/"
        self.post(route, expected=403, **body)
        self.post(route, client=self.manager, expected=400, **{**body, "original_delivery_excluded": False})
        self.post(route, client=self.manager, expected=400, **{**body, "quantity": 101})
        key = str(uuid4())
        result = self.post(route, client=self.manager, expected=200, **body, request_key=key)
        self.assertTrue(self.post(route, client=self.manager, expected=200, **body, request_key=key)["replayed"])
        self.post(route, client=self.manager, expected=400, **body)
        self.assertEqual((result["net_before"], result["net_after"], result["order_status"]), (100, 0, "OPEN"))
        self.assertEqual((self.gross(), QualityReworkCase.objects.count(), QualityReturnAllocation.objects.count()), (100, 0, 0))
        self.assertEqual(order_delivery_totals([self.order.pk])[self.order.pk]["returned_quantity"], 0)
        self.ship(issue_id=issue, expected_packages=0, reused_card_no="SYNTH-WRONG", expected=400)
        self.ship(issue_id=issue, expected_packages=0, customer_card_no="SYNTH-B-OLD", expected=400)
        bid = self.ship(issue_id=issue, expected_packages=0, reused_card_no="SYNTH-B-OLD")["batch_id"]
        record = self.detail(bid)
        self.assertEqual((record["reused_card_no"], record["customer_card_no"], record["claimed_quantity"]), ("SYNTH-B-OLD", "", 0))
        trace = self.client.get(f"{self.endpoint}origins/?code=SYNTH-B-OLD").json()
        self.assertEqual(([o["batch_id"] for o in trace["origins"]], [o["id"] for o in trace["replacements"]]), ([original.pk], [bid]))
        options = self.manager.get(f"{self.endpoint}{bid}/orders/").json()
        self.assertEqual([(o["id"], o["remaining"]) for o in options], [(self.order.pk, 100)])
        ledger = self.client.get("/api/quality/shipment-ledger/?compact=1&q=SYNTH-B-OLD").json()
        self.assertEqual({row["source_id"] for row in ledger["results"]}, {original.pk, bid})
        self.claim(bid, 100, basis="REPLACEMENT_WITHOUT_RETURN")
        self.order.refresh_from_db()
        order_detail = build_order_overview(self.order.pk, section="shipments")
        stats = order_detail["summary"]["delivery_breakdown"]
        self.assertEqual((stats["all_effective_shipments"], stats["delivery_adjustments"], stats["effective_returns"]), (200, 100, 0))
        self.assertEqual(order_detail["supplement_delivery_adjustments"][0]["origin__issue_id"], issue)
        self.assertEqual((self.gross(), self.net(), self.order.status), (200, 100, "COMPLETED"))
        self.assertEqual(list(original.lines.values("id", "piece_quantity", "net_weight_kg")), allocation_snapshot)
        self.assertEqual(ShipmentSupplementEvent.objects.filter(action="DELIVERY_RELEASE").count(), 1)

    def test_partial_release_after_new_package_then_real_return_never_double_deducts(self):
        base, issue, data = self.released_setup()
        bid = self.ship(issue_id=issue, expected_packages=0, reused_card_no="SYNTH-B-OLD")["batch_id"]
        self.claim(bid, 60, basis="REPLACEMENT_WITHOUT_RETURN", expected=400)
        self.assertEqual((self.gross(), self.net()), (200, 100))
        route = f"issues/{issue}/delivery-release/"
        self.post(route, client=self.manager, expected=200, **{**data, "quantity": 60})
        self.claim(bid, 60, basis="REPLACEMENT_WITHOUT_RETURN")
        self.assertEqual((self.gross(), self.net()), (200, 100))
        self.post(route, client=self.manager, expected=400, **{**data, "quantity": 40})
        self.post(route, client=self.manager, expected=200, **{**data, "expected_released": 60, "quantity": 20})
        self.assertEqual(ShipmentSupplementDeliveryAdjustment.objects.get().piece_quantity, 80)
        self.assertEqual(self.net(), 80)
        for action, body in [("amend", {"amend_reason": "合成应阻止改原单"}), ("void-confirmed", {"void_reason": "合成应阻止作废"})]:
            r = self.manager.post(f"{self.batch_endpoint}{base.pk}/{action}/", body, format="json")
            self.assertEqual(r.status_code, 400, r.content)
        case = self.manual_return(base)
        self.assertEqual((self.gross(), self.net(), QualityReturnAllocation.objects.count()), (200, 60, 1))
        self.assertEqual(order_delivery_totals([self.order.pk])[self.order.pk]["returned_quantity"], 100)
        r = self.manager.get(f"{self.return_endpoint}{case.pk}/reship-context/")
        self.assertFalse(r.json()["eligible"])
        last_claim = self.claim(bid, 40, basis="REPLACEMENT_AFTER_RETURN")
        self.assertEqual((self.gross(), self.net()), (200, 100))
        cancelled = self.manager.patch(f"{self.return_endpoint}{case.pk}/", {"status":"CANCELLED"}, format="json")
        self.assertEqual(cancelled.status_code, 400, cancelled.content)
        self.assertIn("已有补发依赖", str(cancelled.json()))
        self.assertEqual((self.gross(), self.net()), (200, 100))
        self.post(f"{bid}/unclaim/", client=self.manager, expected=200, allocation_id=last_claim["allocation_id"], note="合成：撤销依赖该实退的认领")
        cancelled = self.manager.patch(f"{self.return_endpoint}{case.pk}/", {"status":"CANCELLED"}, format="json")
        self.assertEqual(cancelled.status_code, 200, cancelled.content)
        self.assertEqual((self.gross(), self.net(), order_delivery_totals([self.order.pk])[self.order.pk]["returned_quantity"]), (200, 80, 0))

    def test_release_only_selected_unit_share_not_another_item_or_package(self):
        original = self.fixture()
        issue = self.post("issues/", kind="REPLACEMENT", product_id=self.order.product_specification_id,
                          reason="合成单包跨项次", origins=[{"batch_id": original.pk, "unit_no": 1}])["issue_id"]
        issue_url = f"issues/{issue}/"
        options = self.manager.get(self.endpoint + issue_url).json()["release_options"]
        self.assertEqual({x["order_id"]: x["max_quantity"] for x in options}, {self.order.pk: 60, self.target.pk: 40})
        opt = next(x for x in options if x["order_id"] == self.order.pk)
        args = {key: opt[key] for key in ("origin_id", "allocation_id", "expected_released")}
        args.update(quantity=30, original_delivery_excluded=True, note="合成：只扣第一项次30件")
        self.post(issue_url + "delivery-release/", client=self.manager, expected=200, **args)
        self.assertEqual(delivered_quantities_by_order([self.order.pk, self.target.pk]), {self.order.pk: 30, self.target.pk: 40})
        self.assertEqual((self.gross(), QualityReturnAllocation.objects.count()), (100, 0))

    def test_E_unordered_original_and_backfill_reject_same_item_twice(self):
        original = self.ship(customer_card_no="SYNTH-E-OLD")["batch_id"]
        yesterday = timezone.localdate() - timedelta(days=1)
        replacement = self.ship(kind="REPLACEMENT", shipment_date=yesterday, backfill_reason="合成昨日实发",
                                origins=[{"batch_id": original, "unit_no": 1}])["batch_id"]
        self.assertEqual((self.gross(), self.net()), (200, 0))
        self.assertEqual(self.detail(replacement)["date"], yesterday.isoformat())
        self.assertEqual(self.client.get(f"{self.endpoint}origins/?code=SYNTH-E-OLD").json()["replacements"][0]["id"], replacement)
        self.claim(replacement, 100, basis="RECONCILED_SINGLE")
        self.claim(original, 100, expected=400)
        self.assertEqual((self.gross(), self.net()), (200, 100))

    def test_F_return_physical_100_only_charged_60_idempotently(self):
        bid = self.ship()["batch_id"]
        self.claim(bid, 60)
        key = str(uuid4())
        result = self.physical_return(bid, request_key=key)
        self.assertTrue(self.physical_return(bid, request_key=key)["replayed"])
        self.assertEqual((result["physical_return"], result["order_return"], self.gross(), self.net()), (100, 60, 100, 0))
        self.assertEqual((self.detail(bid)["claimable"], self.detail(bid)["returned_quantity"]), (0, 100))
        self.claim(bid, 40, expected=400)
        self.assertFalse(QualityReworkCase.objects.get(pk=result["return_id"]).attempts.exists())
        self.assertEqual(self.client.get(f"{self.return_endpoint}{result['return_id']}/").json()["source"]["pieces_per_batch"], 100)
        detail = self.client.get("/api/quality/summary/").json()
        self.assertEqual(detail["totals"]["returned_quantity"], 100)
        self.assertEqual(detail["order_stats"][0]["returned_quantity"], 60)
        ship_overview = build_order_overview(self.order.pk, section="shipments")["results"][0]
        self.assertEqual((ship_overview["physical_quantity"], ship_overview["own_quantity"]), (100, 60))
        for url, body in (("amend", {"amend_reason": "合成更正"}), ("void-confirmed", {"void_reason": "合成作废"}),
                          ("confirm", {}), ("assign-inspectors", {"inspector_ids": [self.inspector.pk]})):
            r = self.client.post(f"{self.batch_endpoint}{bid}/{url}/", body, format="json")
            self.assertEqual(r.status_code, 400, r.content)
            self.assertIn("无单补货", str(r.json()))

    def test_unknown_count_and_pending_issue_do_not_create_phantom_quantity(self):
        issue = self.post("issues/", kind="REQUEST", product_id=self.order.product_specification_id, reason="合成：尚未发货")
        self.assertEqual(QualityShipmentBatch.objects.count(), 0)
        self.assertEqual(build_workbench(kind="SUPPLEMENT_PENDING")["count"], 1)
        bid = self.ship(issue_id=issue["issue_id"], expected_packages=0, method="WEIGHT", quantity=None,
                        weight_kg="1.300", expected=400)
        self.assertIn("quantity", bid)
        r = self.post(issue_id=issue["issue_id"], expected_packages=0, method="WEIGHT", weight_kg="1.300",
                      shipment_date=timezone.localdate(), inspector_id=self.inspector.pk)
        bid = r["batch_id"]
        self.assertIsNone(self.detail(bid)["quantity"])
        self.assertEqual(self.client.get("/api/quality/summary/").json()["totals"]["pending_piece_packages"], 1)
        self.claim(bid, 1, expected=400)
        self.post(f"{bid}/verify-quantity/", client=self.manager, expected=200, quantity=100, note="合成实点")
        self.assertEqual(self.detail(bid)["quantity"], 100)

    def test_link_already_shipped_without_second_ledger_row(self):
        original = self.ship(customer_card_no="SYNTH-MOVED-OLD")["batch_id"]
        new = self.ship(customer_card_no="SYNTH-MOVED-NEW")["batch_id"]
        issue = self.post("issues/", kind="REPLACEMENT", product_id=self.order.product_specification_id,
                          reason="合成换新", origins=[{"batch_id": original, "unit_no": 1}], requested_quantity=100)
        before = self.detail(new)
        self.post(f"{new}/link-issue/", client=self.manager, expected=200, issue_id=issue["issue_id"],
                  expected_packages=0, note="合成已有送货凭证")
        after = self.detail(new)
        self.assertEqual((before["shipment_no"], before["date"], before["quantity"]),
                         (after["shipment_no"], after["date"], after["quantity"]))
        self.assertEqual((after["kind"], after["issue"]["remaining"], self.gross(), self.net()), ("REPLACEMENT", 0, 200, 0))


@override_settings(QUALITY_SUPPLEMENTS_ENABLED=True)
class SupplementConcurrencyTests(TransactionTestCase):
    def test_concurrent_claims_and_package_resubmission(self):
        from orders.models import ProductSpecification
        factory = get_user_model().objects
        user = factory.create_user("synth-concurrent-employee")
        managers = [factory.create_user(f"synth-concurrent-manager-{idx}", is_staff=True) for idx in (1, 2)]
        product = ProductSpecification.objects.create(specification="SYNTH-CNC", material="SYNTH-MAT")
        order = QualityOrder.objects.create(order_no="SYNTH-CNC", specification=product.specification,
                                           material=product.material, product_specification=product, source_system="翔邦",
                                           order_quantity=200, created_by=user)
        url = SupplementTests.endpoint
        data = {"request_key": str(uuid4()), "shipment_date": timezone.localdate(), "method": "COUNT",
                "quantity": 100, "kind": "REQUEST", "product_id": product.pk, "reason": "合成并发试验"}

        def concurrent(path, requests):
            barrier = Barrier(2)
            def send(pair):
                actor, body = pair
                try:
                    client = APIClient()
                    client.force_authenticate(actor)
                    barrier.wait(timeout=5)
                    response = client.post(path, body, format="json")
                    return response.status_code, response.json()
                finally:
                    close_old_connections()
            with ThreadPoolExecutor(max_workers=2) as executor:
                return list(executor.map(send, requests))

        results = concurrent(url, [(user, data), (user, data)])
        self.assertEqual([code for code, _ in results], [201, 201], results)
        self.assertEqual(len({value["batch_id"] for _, value in results}), 1)
        bid = results[0][1]["batch_id"]
        claims = [(actor, {"request_key": str(uuid4()), "quantity": 60, "order_id": order.pk, "note": "合成并发认领"})
                  for actor in managers]
        results = concurrent(f"{url}{bid}/claim/", claims)
        self.assertEqual(sorted(code for code, _ in results), [200, 400], results)
        self.assertEqual(delivered_quantities_by_order([order.pk])[order.pk], 60)
        self.assertEqual(QualityShipmentBatch.objects.count(), 1)
