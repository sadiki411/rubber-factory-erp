from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from molds.models import MoldModel
from quality.models import (
    ProcessCard,
    ProductUnitWeight,
    QualityReworkAttempt,
    QualityReworkCase,
    QualityShipmentBatch,
)

from .helpers import QualityTestMixin


class HistoricalEntryValidationTests(QualityTestMixin, TestCase):
    """History validation applies to entry/date changes, not the passing of time."""

    def records(self):
        day = timezone.localdate()
        mold = MoldModel.objects.create(code="HISTORY-VALIDATION", product_name="密封圈")
        card = ProcessCard.objects.create(
            card_no="CARD-HISTORY-VALIDATION", order=self.order, quantity=1_000,
            unit_weight_g=Decimal("10"), received_on=day, created_by=self.user,
        )
        case = QualityReworkCase.objects.create(
            origin=QualityReworkCase.Origin.INTERNAL, process_card=card,
            opened_on=day, affected_quantity=100, created_by=self.user,
        )
        return [
            (ProductUnitWeight.objects.create(
                mold_model=mold, unit_weight_g=Decimal("10"), measured_on=day, created_by=self.user,
            ), "measured_on"),
            (card, "received_on"),
            (QualityShipmentBatch.objects.create(
                shipment_date=day, order=self.order, created_by=self.user,
            ), "shipment_date"),
            (case, "opened_on"),
            (QualityReworkAttempt.objects.create(
                case=case, attempt_date=day, input_quantity=100, reworked_quantity=100,
                created_by=self.user,
            ), "attempt_date"),
        ]

    def test_existing_records_remain_editable_after_several_days(self):
        day = timezone.localdate()
        records = self.records()
        with patch("django.utils.timezone.localdate", return_value=day + timedelta(days=3)):
            for record, date_field in records:
                with self.subTest(model=type(record).__name__):
                    # Reload: this must also work without an in-memory date snapshot.
                    record.refresh_from_db()
                    record.notes = "正常跨日编辑"
                    record.save(update_fields=["notes", "updated_at"])
                    record.refresh_from_db()
                    self.assertEqual(record.notes, "正常跨日编辑")
                    self.assertEqual(getattr(record, date_field), day)
                    self.assertEqual(record.backfill_reason, "")

    def test_new_historical_records_require_a_reason(self):
        day = timezone.localdate()
        records = self.records()
        with patch("django.utils.timezone.localdate", return_value=day + timedelta(days=3)):
            for record, _ in records:
                with self.subTest(model=type(record).__name__):
                    record.pk = None
                    record._state.adding = True
                    with self.assertRaises(ValidationError) as caught:
                        record.clean()
                    self.assertIn("backfill_reason", caught.exception.message_dict)
                    record.backfill_reason = "历史纸质记录补录"
                    record.clean()

    def test_changing_an_existing_business_date_into_the_past_requires_a_reason(self):
        day = timezone.localdate()
        for record, date_field in self.records():
            with self.subTest(model=type(record).__name__):
                setattr(record, date_field, day - timedelta(days=1))
                with self.assertRaises(ValidationError) as caught:
                    record.save()
                self.assertIn("backfill_reason", caught.exception.message_dict)
                stored = type(record).objects.get(pk=record.pk)
                self.assertEqual(getattr(stored, date_field), day)
                record.backfill_reason = "核对原始日期后更正"
                record.save()
                record.refresh_from_db()
                self.assertEqual(getattr(record, date_field), day - timedelta(days=1))

    def test_changing_an_already_old_date_still_requires_a_reason(self):
        day = timezone.localdate()
        records = self.records()
        with patch("django.utils.timezone.localdate", return_value=day + timedelta(days=3)):
            for record, date_field in records:
                with self.subTest(model=type(record).__name__):
                    setattr(record, date_field, day + timedelta(days=1))
                    with self.assertRaises(ValidationError) as caught:
                        record.clean()
                    self.assertIn("backfill_reason", caught.exception.message_dict)

    def test_existing_historical_reason_cannot_be_erased(self):
        day = timezone.localdate()
        records = self.records()
        for record, date_field in records:
            setattr(record, date_field, day - timedelta(days=1))
            record.backfill_reason = "历史纸质记录补录"
            record.save()
            with self.subTest(model=type(record).__name__):
                record.backfill_reason = "  "
                with self.assertRaises(ValidationError) as caught:
                    record.save()
                self.assertIn("backfill_reason", caught.exception.message_dict)
                record.refresh_from_db()
                self.assertEqual(record.backfill_reason, "历史纸质记录补录")

    def test_new_blank_or_approximate_return_date_requires_a_reason(self):
        case = self.records()[3][0]
        for values in ({"opened_on": None}, {"date_is_approximate": True}):
            with self.subTest(values=values):
                new_case = QualityReworkCase(
                    origin=QualityReworkCase.Origin.INTERNAL, process_card=case.process_card,
                    created_by=self.user, **values,
                )
                with self.assertRaises(ValidationError) as caught:
                    new_case.clean()
                self.assertIn("backfill_reason", caught.exception.message_dict)
                new_case.backfill_reason = "旧记录日期不确定"
                new_case.clean()

    def test_changing_return_date_to_unknown_or_approximate_requires_a_reason(self):
        case = self.records()[3][0]
        for values in ({"opened_on": None}, {"date_is_approximate": True}):
            with self.subTest(values=values):
                case.refresh_from_db()
                for key, value in values.items():
                    setattr(case, key, value)
                with self.assertRaises(ValidationError) as caught:
                    case.save()
                self.assertIn("backfill_reason", caught.exception.message_dict)

    def test_new_record_with_explicit_pk_does_not_bypass_history_validation(self):
        day = timezone.localdate()
        case = self.records()[3][0]
        case.pk += 10_000
        case._state.adding = False
        case.opened_on = day - timedelta(days=1)
        with self.assertRaises(ValidationError) as caught:
            case.clean()
        self.assertIn("backfill_reason", caught.exception.message_dict)
