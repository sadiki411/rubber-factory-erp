"""Helpers for the finished-product unit weight remembered by shipping.

``measured_on`` is the business date of a measurement, while ``created_at``
records when an operator saved it in ERP.  Shipment entry needs the latter:
the last confirmed value must become the default for the next shipment even
when an older value (for example 8 g after 9 g) is selected again or a
historical shipment date is being entered.
"""

from decimal import Decimal

from django.utils import timezone

from .models import ProductUnitWeight


def latest_saved_product_unit_weight(
    *,
    product_specification_id=None,
    specification="",
    material="",
):
    """Return the last active unit weight saved for an exact product identity."""

    queryset = ProductUnitWeight.objects.filter(
        is_active=True,
        unit_weight_g__gt=0,
    )
    if product_specification_id:
        queryset = queryset.filter(
            product_specification_id=product_specification_id
        )
    else:
        specification = str(specification or "").strip()
        material = str(material or "").strip()
        if not specification and not material:
            return None
        queryset = queryset.filter(
            product_specification__specification__iexact=specification,
            product_specification__material__iexact=material,
        )
    return queryset.order_by("-created_at", "-id").first()


def remember_confirmed_product_unit_weight(
    *,
    product_specification_id,
    unit_weight_g,
    created_by,
    measured_on=None,
    note="由出货确认自动保存的单重历史",
):
    """Make a confirmed shipment value the default for future shipments.

    A value equal to the current default is reused to avoid duplicate history.
    When the operator deliberately changes 9 g back to a previously-used 8 g,
    a new 8 g history row is required; reusing the old row would incorrectly
    leave 9 g as the next default.
    """

    if not product_specification_id or not unit_weight_g or not created_by:
        return None
    value = Decimal(unit_weight_g).quantize(Decimal("0.00001"))
    latest = latest_saved_product_unit_weight(
        product_specification_id=product_specification_id
    )
    if latest is not None and latest.unit_weight_g == value:
        return latest
    return ProductUnitWeight.objects.create(
        product_specification_id=product_specification_id,
        unit_weight_g=value,
        is_active=True,
        measured_on=measured_on or timezone.localdate(),
        backfill_reason="出货确认自动保存历史单重",
        created_by=created_by,
        notes=note,
    )
