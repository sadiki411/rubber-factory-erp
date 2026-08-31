from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.test import TestCase
from django.utils import timezone

from quality.models import (
    ProcessCard,
    ProductUnitWeight,
    QualityOrder,
    QualityReworkCase,
    QualityShipmentBatch,
    QualityShipmentLine,
    QualityShipmentOrderAllocation,
)

from .helpers import QualityTestMixin, response_results


class OrderAutoAllocationApiTests(QualityTestMixin, TestCase):
    def create_order(self, order_no, quantity, *, due_days=10, **overrides):
        values = {
            "order_no": order_no,
            "product_name": "橡胶密封圈",
            "specification": self.order.specification,
            "material": self.order.material,
            "order_quantity": quantity,
            "order_date": timezone.localdate(),
            "due_date": timezone.localdate() + timedelta(days=due_days),
            "created_by": self.user,
        }
        values.update(overrides)
        return QualityOrder.objects.create(**values)

    def repeat_draft(
        self,
        *,
        order=None,
        single_weight="0.100",
        unit="1",
        batch_count=1,
        standard=100,
        shipment_no=None,
    ):
        target = order or self.order
        response = self.client.post(
            "/api/quality/shipment-batches/",
            {
                "shipment_no": shipment_no or "",
                "shipment_date": timezone.localdate().isoformat(),
                "order_id": target.pk,
                "specification_snapshot": target.specification,
                "material_snapshot": target.material,
                "unit_weight_g": unit,
                "single_batch_net_weight_kg": single_weight,
                "product_batch_count": batch_count,
                "process_card_shipment_quantity": standard,
                "lines": [{"order_id": target.pk}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        return response

    def confirm(self, batch_id):
        return self.client.post(
            f"/api/quality/shipment-batches/{batch_id}/confirm/",
            {},
            format="json",
        )

    def line_quantities(self, batch_id):
        return {
            row["order_id"]: row["total"]
            for row in QualityShipmentOrderAllocation.objects.filter(
                shipment_line__batch_id=batch_id
            )
            .values("order_id")
            .annotate(total=Sum("piece_quantity"))
        }

    def test_two_thousand_by_ten_fills_same_specification_order_and_is_idempotent(self):
        self.order.order_quantity = 10_000
        self.order.save()
        second = self.create_order("ORD-AUTO-SECOND", 10_000, due_days=2)

        preview = self.client.post(
            "/api/quality/shipment-batches/allocation-preview/",
            {"order_id": self.order.pk, "piece_quantity": 20_000},
            format="json",
        )
        self.assertEqual(preview.status_code, 200, preview.content)
        self.assertEqual(
            [item["allocated_quantity"] for item in preview.json()["allocations"]],
            [10_000, 10_000],
        )

        draft = self.repeat_draft(
            single_weight="2.000",
            unit="1",
            batch_count=10,
            standard=2_000,
            shipment_no="AUTO-2K-X10",
        )
        confirmed = self.confirm(draft.json()["id"])
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        self.assertEqual(
            self.line_quantities(draft.json()["id"]),
            {self.order.pk: 10_000, second.pk: 10_000},
        )
        lines = QualityShipmentLine.objects.filter(batch_id=draft.json()["id"])
        self.assertEqual(lines.count(), 1)
        self.assertEqual(
            sum((line.net_weight_kg for line in lines), Decimal("0")),
            Decimal("20.000"),
        )
        self.assertEqual(sum(line.piece_quantity for line in lines), 20_000)
        self.assertTrue(all(line.single_batch_net_weight_kg is not None for line in lines))
        self.assertTrue(all(line.product_specification_id for line in lines))
        self.assertTrue(
            all(
                ProductUnitWeight.objects.filter(
                    product_specification_id=line.product_specification_id,
                    unit_weight_g=Decimal("1.00000"),
                    is_active=True,
                ).exists()
                for line in lines
            )
        )

        retried = self.confirm(draft.json()["id"])
        self.assertEqual(retried.status_code, 200, retried.content)
        self.assertEqual(QualityShipmentLine.objects.filter(batch_id=draft.json()["id"]).count(), 1)

        candidates = response_results(
            self.client.get(
                "/api/quality/shipment-batches/candidates/",
                {
                    "specification": self.order.specification,
                    "material": self.order.material,
                },
            )
        )
        self.assertEqual(candidates, [])

    def test_partial_candidates_are_filled_by_due_date_then_order_date(self):
        self.order.order_quantity = 100
        self.order.save()
        later = self.create_order("ORD-AUTO-LATER", 200, due_days=8)
        earlier = self.create_order("ORD-AUTO-EARLIER", 50, due_days=1)
        self.create_shipment(
            shipment_no="LEGACY-EARLY-20",
            order=earlier,
            inspection_quantity=20,
            qualified_quantity=20,
            defective_quantity=0,
            shipped_quantity=20,
        )

        draft = self.repeat_draft(batch_count=3, shipment_no="AUTO-DUE-ORDER")
        confirmed = self.confirm(draft.json()["id"])
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        self.assertEqual(
            self.line_quantities(draft.json()["id"]),
            {self.order.pk: 100, earlier.pk: 30, later.pk: 170},
        )
        ordered_lines = list(
            QualityShipmentOrderAllocation.objects.filter(
                shipment_line__batch_id=draft.json()["id"]
            ).order_by("shipment_line_id", "sequence")
        )
        self.assertEqual(
            [line.order_id for line in ordered_lines],
            [self.order.pk, earlier.pk, later.pk],
        )

    def test_same_due_date_uses_order_date_then_id(self):
        self.order.order_quantity = 100
        self.order.save()
        due_date = timezone.localdate() + timedelta(days=5)
        late = self.create_order(
            "ORD-SORT-LATE",
            100,
            due_date=due_date,
            order_date=timezone.localdate(),
        )
        early_first = self.create_order(
            "ORD-SORT-EARLY-1",
            100,
            due_date=due_date,
            order_date=timezone.localdate() - timedelta(days=2),
        )
        early_second = self.create_order(
            "ORD-SORT-EARLY-2",
            100,
            due_date=due_date,
            order_date=timezone.localdate() - timedelta(days=2),
        )
        preview = self.client.post(
            "/api/quality/shipment-batches/allocation-preview/",
            {"order_id": self.order.pk, "piece_quantity": 350},
            format="json",
        )
        self.assertEqual(preview.status_code, 200, preview.content)
        self.assertEqual(
            [item["order_id"] for item in preview.json()["allocations"]],
            [self.order.pk, early_first.pk, early_second.pk, late.pk],
        )
        self.assertEqual(
            [item["allocated_quantity"] for item in preview.json()["allocations"]],
            [100, 100, 100, 50],
        )

    def test_matching_capacity_shortage_returns_residue_to_one_source_line(self):
        self.order.order_quantity = 100
        self.order.save()
        second = self.create_order("ORD-AUTO-SHORT", 50, due_days=1)

        draft = self.repeat_draft(batch_count=3, shipment_no="AUTO-SHORTAGE")
        confirmed = self.confirm(draft.json()["id"])
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        self.assertEqual(
            self.line_quantities(draft.json()["id"]),
            {self.order.pk: 250, second.pk: 50},
        )
        allocations = list(
            QualityShipmentOrderAllocation.objects.filter(
                shipment_line__batch_id=draft.json()["id"]
            ).order_by("sequence")
        )
        self.assertEqual(
            [item.order_id for item in allocations],
            [self.order.pk, second.pk, self.order.pk],
        )
        self.assertEqual([item.is_overflow for item in allocations], [False, False, True])
        self.assertEqual(
            sum(
                (
                    line.net_weight_kg
                    for line in QualityShipmentLine.objects.filter(
                        batch_id=draft.json()["id"]
                    )
                ),
                Decimal("0"),
            ),
            Decimal("0.300"),
        )

    def test_no_matching_order_allows_source_to_exceed_order_quantity(self):
        self.order.order_quantity = 100
        self.order.save()
        draft = self.repeat_draft(batch_count=2, shipment_no="AUTO-NO-MATCH")
        confirmed = self.confirm(draft.json()["id"])
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        line = QualityShipmentLine.objects.get(batch_id=draft.json()["id"])
        self.assertEqual(line.order_id, self.order.pk)
        self.assertEqual(line.piece_quantity, 200)
        # No split occurred, so the physical repeat-weighing facts remain on
        # the original line and the per-batch 110% validation remains active.
        self.assertEqual(line.product_batch_count, 2)
        self.assertEqual(line.process_card_shipment_quantity, 100)

    def test_non_integral_weight_split_preserves_exact_totals(self):
        self.order.order_quantity = 101
        self.order.save()
        second = self.create_order("ORD-ROUNDING", 103, due_days=1)
        draft = self.repeat_draft(
            single_weight="1.000",
            unit="3",
            batch_count=1,
            standard=333,
            shipment_no="AUTO-ROUNDING",
        )
        confirmed = self.confirm(draft.json()["id"])
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        lines = list(QualityShipmentLine.objects.filter(batch_id=draft.json()["id"]))
        self.assertEqual(
            self.line_quantities(draft.json()["id"]),
            {self.order.pk: 230, second.pk: 103},
        )
        self.assertEqual(sum(line.piece_quantity for line in lines), 333)
        self.assertEqual(
            sum((line.net_weight_kg for line in lines), Decimal("0")),
            Decimal("1.000"),
        )

    def test_blank_identity_never_cross_allocates(self):
        self.order.order_quantity = 100
        self.order.material = ""
        self.order.save()
        self.create_order("ORD-BLANK-MATERIAL", 100, material="", due_days=1)

        draft = self.repeat_draft(batch_count=2, shipment_no="AUTO-BLANK")
        confirmed = self.confirm(draft.json()["id"])
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        self.assertEqual(
            self.line_quantities(draft.json()["id"]), {self.order.pk: 200}
        )

    def test_different_material_specification_and_closed_orders_are_not_candidates(self):
        self.order.order_quantity = 100
        self.order.save()
        self.create_order("ORD-DIFFERENT-MATERIAL", 100, material="EPDM", due_days=1)
        self.create_order("ORD-DIFFERENT-SPEC", 100, specification="DIFFERENT", due_days=1)
        self.create_order(
            "ORD-COMPLETED",
            100,
            status=QualityOrder.Status.COMPLETED,
            due_days=1,
        )
        self.create_order(
            "ORD-CANCELLED",
            100,
            status=QualityOrder.Status.CANCELLED,
            due_days=1,
        )
        draft = self.repeat_draft(batch_count=2, shipment_no="AUTO-EXCLUDE")
        self.assertEqual(self.confirm(draft.json()["id"]).status_code, 200)
        self.assertEqual(
            self.line_quantities(draft.json()["id"]), {self.order.pk: 200}
        )

    def test_completed_source_is_identity_anchor_but_cancelled_source_is_rejected(self):
        draft = self.repeat_draft(shipment_no="AUTO-CLOSED-SOURCE")
        second = self.create_order("ORD-AFTER-COMPLETED", 100, due_days=1)
        self.order.status = QualityOrder.Status.COMPLETED
        self.order.save()
        preview = self.client.post(
            "/api/quality/shipment-batches/allocation-preview/",
            {"order_id": self.order.pk, "piece_quantity": 100},
            format="json",
        )
        self.assertEqual(preview.status_code, 200, preview.content)
        self.assertEqual(preview.json()["allocations"][0]["order_id"], second.pk)
        confirmed = self.confirm(draft.json()["id"])
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        self.assertEqual(self.line_quantities(draft.json()["id"]), {second.pk: 100})
        cancelled_draft = self.repeat_draft(
            order=second, shipment_no="AUTO-CANCELLED-SOURCE"
        )
        second.status = QualityOrder.Status.CANCELLED
        second.save()
        cancelled = self.confirm(cancelled_draft.json()["id"])
        self.assertEqual(cancelled.status_code, 400, cancelled.content)
        batch = QualityShipmentBatch.objects.get(pk=cancelled_draft.json()["id"])
        self.assertEqual(batch.status, QualityShipmentBatch.Status.DRAFT)

    def test_legacy_draft_with_only_batch_unit_weight_can_be_allocated(self):
        self.order.order_quantity = 100
        self.order.save()
        second = self.create_order("ORD-HEADER-UNIT", 100, due_days=1)
        batch = QualityShipmentBatch.objects.create(
            shipment_no="AUTO-HEADER-UNIT",
            shipment_date=timezone.localdate(),
            order=self.order,
            unit_weight_g=Decimal("1"),
            created_by=self.user,
        )
        # This reproduces a rolling-upgrade draft written before line-level
        # unit snapshots became mandatory.  bulk_create intentionally bypasses
        # today's stricter model validation.
        QualityShipmentLine.objects.bulk_create(
            [
                QualityShipmentLine(
                    batch=batch,
                    order=self.order,
                    specification_snapshot=self.order.specification,
                    material_snapshot=self.order.material,
                    net_weight_kg=Decimal("0.200"),
                    piece_quantity=200,
                    unit_weight_g_snapshot=None,
                )
            ]
        )
        confirmed = self.confirm(batch.pk)
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        self.assertEqual(
            self.line_quantities(batch.pk),
            {self.order.pk: 100, second.pk: 100},
        )
        self.assertTrue(
            all(
                line.unit_weight_g_snapshot == Decimal("1.00000")
                for line in QualityShipmentLine.objects.filter(batch=batch)
            )
        )

    def test_legacy_draft_without_any_unit_weight_returns_validation_error(self):
        self.order.order_quantity = 100
        self.order.save()
        self.create_order("ORD-MISSING-UNIT", 100, due_days=1)
        batch = QualityShipmentBatch.objects.create(
            shipment_no="AUTO-MISSING-UNIT",
            shipment_date=timezone.localdate(),
            order=self.order,
            unit_weight_g=None,
            created_by=self.user,
        )
        QualityShipmentLine.objects.bulk_create(
            [
                QualityShipmentLine(
                    batch=batch,
                    order=self.order,
                    specification_snapshot=self.order.specification,
                    material_snapshot=self.order.material,
                    net_weight_kg=Decimal("0.200"),
                    piece_quantity=200,
                    unit_weight_g_snapshot=None,
                )
            ]
        )
        confirmed = self.confirm(batch.pk)
        self.assertEqual(confirmed.status_code, 400, confirmed.content)
        self.assertIn("成品单重", str(confirmed.json()))
        batch.refresh_from_db()
        self.assertEqual(batch.status, QualityShipmentBatch.Status.DRAFT)
        self.assertEqual(batch.lines.count(), 1)

    def test_line_only_repeat_facts_are_preserved_on_batch_when_split(self):
        self.order.order_quantity = 100
        self.order.save()
        self.create_order("ORD-LINE-ONLY-REPEAT", 100, due_days=1)
        draft = self.client.post(
            "/api/quality/shipment-batches/",
            {
                "shipment_no": "AUTO-LINE-ONLY-REPEAT",
                "shipment_date": timezone.localdate().isoformat(),
                "lines": [
                    {
                        "order_id": self.order.pk,
                        "unit_weight_g": "1",
                        "single_batch_net_weight_kg": "0.100",
                        "product_batch_count": 2,
                        "process_card_shipment_quantity": 100,
                    }
                ],
            },
            format="json",
        )
        self.assertEqual(draft.status_code, 201, draft.content)
        self.assertIsNone(draft.json()["single_batch_net_weight_kg"])
        confirmed = self.confirm(draft.json()["id"])
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        batch = QualityShipmentBatch.objects.get(pk=draft.json()["id"])
        self.assertEqual(batch.single_batch_net_weight_kg, Decimal("0.100"))
        self.assertEqual(batch.process_card_shipment_quantity, 100)
        self.assertEqual(batch.product_batch_count, 2)
        self.assertEqual(batch.pieces_per_batch, 100)
        line = batch.lines.get()
        self.assertEqual(line.single_batch_net_weight_kg, Decimal("0.100"))
        self.assertEqual(line.process_card_shipment_quantity, 100)
        self.assertEqual(line.product_batch_count, 2)
        self.assertEqual(line.pieces_per_batch, 100)

    def test_legacy_returns_use_same_balance_in_candidate_and_order_detail(self):
        shipment = self.create_shipment(
            shipment_no="LEGACY-BALANCE",
            inspection_quantity=100,
            qualified_quantity=100,
            defective_quantity=0,
            shipped_quantity=100,
        )
        self.create_rework(
            shipment=shipment,
            returned_quantity=30,
            reworked_quantity=30,
            recovered_quantity=30,
            scrap_quantity=0,
        )
        candidate = response_results(
            self.client.get(
                "/api/quality/shipment-batches/candidates/",
                {"order_no": self.order.order_no},
            )
        )[0]
        self.assertEqual(candidate["remaining_quantity"], 930)
        order_payload = self.client.get(
            f"/api/orders/orders/{self.order.pk}/"
        ).json()
        self.assertEqual(order_payload["weighted_shipped_quantity"], 70)
        self.assertEqual(order_payload["weighted_remaining_quantity"], 930)
        self.assertEqual(order_payload["shipment_status"], "PARTIAL")

    def test_return_reopens_only_allocated_target_and_cancel_closes_it_again(self):
        self.order.order_quantity = 100
        self.order.save()
        second = self.create_order("ORD-RETURN-TARGET", 100, due_days=1)
        draft = self.repeat_draft(batch_count=2, shipment_no="AUTO-RETURN-TARGET")
        self.assertEqual(self.confirm(draft.json()["id"]).status_code, 200)
        line = QualityShipmentLine.objects.get(batch_id=draft.json()["id"])
        returned = self.client.post(
            "/api/quality/rework-cases/",
            {
                "origin": QualityReworkCase.Origin.CUSTOMER_RETURN,
                "shipment_batch_id": draft.json()["id"],
                "shipment_unit_no": 2,
                "reason_category": "OTHER",
            },
            format="json",
        )
        self.assertEqual(returned.status_code, 201, returned.content)
        internal = self.client.post(
            "/api/quality/rework-cases/",
            {
                "origin": QualityReworkCase.Origin.INTERNAL,
                "shipment_line_id": line.pk,
                "affected_quantity": 20,
            },
            format="json",
        )
        self.assertEqual(internal.status_code, 201, internal.content)
        candidates = response_results(
            self.client.get(
                "/api/quality/shipment-batches/candidates/",
                {
                    "specification": self.order.specification,
                    "material": self.order.material,
                },
            )
        )
        self.assertEqual(
            [(item["order_id"], item["remaining_quantity"]) for item in candidates],
            [(second.pk, 100)],
        )

        cancelled = self.client.patch(
            f"/api/quality/rework-cases/{returned.json()['id']}/",
            {"status": QualityReworkCase.Status.CANCELLED},
            format="json",
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.content)
        second.refresh_from_db()
        self.assertEqual(second.status, QualityOrder.Status.COMPLETED)
        candidates = response_results(
            self.client.get(
                "/api/quality/shipment-batches/candidates/",
                {
                    "specification": self.order.specification,
                    "material": self.order.material,
                },
            )
        )
        self.assertEqual(candidates, [])

    def test_draft_and_void_batches_never_consume_candidate_balance(self):
        draft = self.repeat_draft(shipment_no="AUTO-DRAFT-BALANCE")
        before = response_results(
            self.client.get(
                "/api/quality/shipment-batches/candidates/",
                {"order_no": self.order.order_no},
            )
        )[0]
        self.assertEqual(before["remaining_quantity"], self.order.order_quantity)
        voided = self.client.post(
            f"/api/quality/shipment-batches/{draft.json()['id']}/void/",
            {},
            format="json",
        )
        self.assertEqual(voided.status_code, 200, voided.content)
        after = response_results(
            self.client.get(
                "/api/quality/shipment-batches/candidates/",
                {"order_no": self.order.order_no},
            )
        )[0]
        self.assertEqual(after["remaining_quantity"], self.order.order_quantity)

    def test_explicit_multiple_direct_lines_share_one_transaction_balance(self):
        second = self.create_order("ORD-EXPLICIT-SECOND", 50, due_days=1)
        response = self.client.post(
            "/api/quality/shipment-batches/",
            {
                "shipment_no": "AUTO-EXPLICIT-MULTI",
                "shipment_date": timezone.localdate().isoformat(),
                "lines": [
                    {
                        "order_id": self.order.pk,
                        "unit_weight_g": "1",
                        "net_weight_kg": "0.100",
                    },
                    {
                        "order_id": second.pk,
                        "unit_weight_g": "1",
                        "net_weight_kg": "0.100",
                    },
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        confirmed = self.confirm(response.json()["id"])
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        self.assertEqual(
            self.line_quantities(response.json()["id"]),
            {self.order.pk: 150, second.pk: 50},
        )

    def test_multiple_scanned_card_lines_allocate_in_physical_order(self):
        self.order.order_quantity = 100
        self.order.save(update_fields=["order_quantity", "updated_at"])
        second = self.create_order("ORD-CARD-OVERFLOW", 100, due_days=1)
        cards = [
            ProcessCard.objects.create(
                card_no=f"PC-ALLOC-{index}",
                order=self.order,
                quantity=100,
                unit_weight_g=Decimal("1.00000"),
                created_by=self.user,
            )
            for index in (1, 2)
        ]
        draft = self.client.post(
            "/api/quality/shipment-batches/",
            {
                "shipment_no": "AUTO-MULTI-CARD",
                "shipment_date": timezone.localdate().isoformat(),
                "lines": [
                    {
                        "process_card_id": card.pk,
                        "net_weight_kg": "0.100",
                        "piece_quantity": 100,
                    }
                    for card in cards
                ],
            },
            format="json",
        )
        self.assertEqual(draft.status_code, 201, draft.content)
        confirmed = self.confirm(draft.json()["id"])
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        physical_lines = list(
            QualityShipmentLine.objects.filter(batch_id=draft.json()["id"])
            .prefetch_related("order_allocations")
            .order_by("id")
        )
        self.assertEqual(len(physical_lines), 2)
        self.assertEqual(
            [line.process_card_id for line in physical_lines],
            [cards[0].pk, cards[1].pk],
        )
        self.assertEqual(
            [
                [
                    (item.order_id, item.piece_quantity)
                    for item in line.order_allocations.all()
                ]
                for line in physical_lines
            ],
            [[(self.order.pk, 100)], [(second.pk, 100)]],
        )
        response_lines = confirmed.json()["lines"]
        self.assertEqual(
            [row["order_allocations"][0]["order_id"] for row in response_lines],
            [self.order.pk, second.pk],
        )

    def test_completed_source_card_stays_bound_when_whole_package_moves_next(self):
        second = self.create_order("ORD-COMPLETED-CARD-NEXT", 100, due_days=1)
        card = ProcessCard.objects.create(
            card_no="PC-COMPLETED-SOURCE",
            order=self.order,
            quantity=100,
            unit_weight_g=Decimal("1.00000"),
            created_by=self.user,
        )
        self.order.status = QualityOrder.Status.COMPLETED
        self.order.save(update_fields=["status", "updated_at"])
        draft = self.client.post(
            "/api/quality/shipment-batches/",
            {
                "shipment_no": "AUTO-COMPLETED-SOURCE-CARD",
                "shipment_date": timezone.localdate().isoformat(),
                "lines": [
                    {
                        "process_card_id": card.pk,
                        "net_weight_kg": "0.100",
                        "piece_quantity": 100,
                    }
                ],
            },
            format="json",
        )
        self.assertEqual(draft.status_code, 201, draft.content)
        confirmed = self.confirm(draft.json()["id"])
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        line = QualityShipmentLine.objects.get(batch_id=draft.json()["id"])
        allocation = line.order_allocations.get()
        self.assertEqual((allocation.order_id, allocation.piece_quantity), (second.pk, 100))
        binding = card.unit_binding
        self.assertEqual(binding.shipment_batch_id, draft.json()["id"])
        self.assertEqual(binding.shipment_unit_no, 1)
        card.refresh_from_db()
        self.assertEqual(card.order_id, self.order.pk)
        for params in (
            {"q": second.order_no},
            {"order_id": second.pk},
            {"order": second.order_no},
            {
                "due_date_from": second.due_date.isoformat(),
                "due_date_to": second.due_date.isoformat(),
            },
            {"order_status": QualityOrder.Status.COMPLETED},
            {"delivery_status": "SHIPPED"},
        ):
            rows = response_results(
                self.client.get("/api/quality/shipment-batches/", params)
            )
            self.assertEqual([row["id"] for row in rows], [draft.json()["id"]])
        ordered = response_results(
            self.client.get(
                "/api/quality/shipment-batches/", {"ordering": "due_date"}
            )
        )
        self.assertEqual([row["id"] for row in ordered], [draft.json()["id"]])
