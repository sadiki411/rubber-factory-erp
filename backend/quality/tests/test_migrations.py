from decimal import Decimal
from importlib import import_module

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ProcessCardTrackingMigrationTests(TransactionTestCase):
    """Regression coverage for populated production databases."""

    migrate_from = ("quality", "0010_alter_qualityreworkcase_reason_category_and_more")
    migrate_to = ("quality", "0011_defectreason_processcardunitbinding_and_more")

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        old_apps = executor.loader.project_state([self.migrate_from]).apps

        User = old_apps.get_model("auth", "User")
        Order = old_apps.get_model("quality", "QualityOrder")
        ProcessCard = old_apps.get_model("quality", "ProcessCard")
        ReworkCase = old_apps.get_model("quality", "QualityReworkCase")
        user = User.objects.create(username="migration-user")
        order = Order.objects.create(
            order_no="MIGRATION-ORDER",
            item_no="1",
            product_name="迁移测试产品",
            specification="MIGRATION-SPEC",
            material="NBR",
            order_quantity=200,
            created_by_id=user.pk,
        )
        self.card_ids = [
            ProcessCard.objects.create(
                card_no=f"MIGRATION-CARD-{index}",
                order_id=order.pk,
                quantity=100,
                unit_weight_g=Decimal("1.00000"),
                created_by_id=user.pk,
            ).pk
            for index in (1, 2)
        ]
        self.return_case_ids = [
            ReworkCase.objects.create(
                case_no=f"MIGRATION-RETURN-{index}",
                origin="CUSTOMER_RETURN",
                process_card_id=self.card_ids[0],
                status=status,
                reason="旧数据",
                created_by_id=user.pk,
            ).pk
            for index, status in ((1, "COMPLETED"), (2, "OPEN"))
        ]

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        self.apps = executor.loader.project_state([self.migrate_to]).apps

    def test_existing_cards_receive_distinct_non_null_tracking_ids(self):
        ProcessCard = self.apps.get_model("quality", "ProcessCard")
        values = list(
            ProcessCard.objects.filter(pk__in=self.card_ids)
            .order_by("pk")
            .values_list("tracking_id", flat=True)
        )

        self.assertEqual(len(values), 2)
        self.assertTrue(all(values))
        self.assertNotEqual(values[0], values[1])

    def test_existing_customer_returns_are_numbered_and_only_latest_is_current(self):
        ReworkCase = self.apps.get_model("quality", "QualityReworkCase")
        values = list(
            ReworkCase.objects.filter(pk__in=self.return_case_ids)
            .order_by("pk")
            .values_list("return_round", "is_current_return")
        )

        self.assertEqual(values, [(1, False), (2, True)])


class ShipmentOrderAllocationMigrationTests(TransactionTestCase):
    migrate_from = (
        "quality",
        "0011_defectreason_processcardunitbinding_and_more",
    )
    migrate_to = ("quality", "0012_shipment_order_allocations")

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        old_apps = executor.loader.project_state([self.migrate_from]).apps
        User = old_apps.get_model("auth", "User")
        Order = old_apps.get_model("quality", "QualityOrder")
        Batch = old_apps.get_model("quality", "QualityShipmentBatch")
        Line = old_apps.get_model("quality", "QualityShipmentLine")
        user = User.objects.create(username="shipment-allocation-migration")
        order = Order.objects.create(
            order_no="MIGRATION-SHIPMENT-ORDER",
            item_no="7",
            specification="20x30",
            material="NBR",
            order_quantity=500,
            created_by_id=user.pk,
        )
        batch = Batch.objects.create(
            shipment_no="MIGRATION-CONFIRMED-SHIPMENT",
            status="CONFIRMED",
            order_id=order.pk,
            created_by_id=user.pk,
        )
        self.line_id = Line.objects.create(
            batch_id=batch.pk,
            order_id=order.pk,
            specification_snapshot=order.specification,
            material_snapshot=order.material,
            net_weight_kg=Decimal("1.000"),
            piece_quantity=100,
            unit_weight_g_snapshot=Decimal("10.00000"),
        ).pk
        # Some very old/partially repaired databases can contain a confirmed
        # physical row without an order association.  The migration must keep
        # production available and leave that untouched row on the runtime
        # fallback instead of guessing an order or aborting startup.
        invalid_line = Line(
            batch_id=batch.pk,
            order_id=None,
            specification_snapshot=order.specification,
            material_snapshot=order.material,
            net_weight_kg=Decimal("0.100"),
            piece_quantity=10,
            unit_weight_g_snapshot=Decimal("10.00000"),
        )
        Line.objects.bulk_create([invalid_line])
        self.invalid_line_id = invalid_line.pk
        self.order_id = order.pk

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        self.apps = executor.loader.project_state([self.migrate_to]).apps

    def test_confirmed_history_receives_one_equivalent_allocation_idempotently(self):
        Allocation = self.apps.get_model(
            "quality", "QualityShipmentOrderAllocation"
        )
        allocation = Allocation.objects.get(shipment_line_id=self.line_id)
        self.assertEqual(allocation.order_id, self.order_id)
        self.assertEqual(allocation.piece_quantity, 100)
        self.assertEqual(allocation.net_weight_kg, Decimal("1.000"))
        self.assertEqual((allocation.piece_start, allocation.piece_end), (0, 100))

        migration = import_module(
            "quality.migrations.0012_shipment_order_allocations"
        )
        with connection.schema_editor() as schema_editor:
            migration.backfill_confirmed_shipment_allocations(
                self.apps, schema_editor
            )
        self.assertEqual(
            Allocation.objects.filter(shipment_line_id=self.line_id).count(), 1
        )
        self.assertFalse(
            Allocation.objects.filter(shipment_line_id=self.invalid_line_id).exists()
        )
