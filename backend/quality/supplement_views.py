from django.db.models import Q, Sum
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from . import supplements as service
from .models import QualityOrder, QualityShipment, QualityShipmentBatch, ShipmentSupplement, ShipmentSupplementIssue
from .views import QualityPagination, quality_write_retry


def _enabled():
    if not service.enabled():
        raise NotFound("翔邦无订单补货未启用。")


def _payload(request):
    serializer = service.CommandSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


class SupplementConfigView(APIView):
    def get(self, request):
        return Response({"enabled": service.enabled(), "can_reconcile": service.enabled() and service.can_reconcile(request.user)})


class SupplementListView(APIView):
    def get(self, request):
        _enabled()
        queryset = ShipmentSupplement.objects.select_related("batch__inspector", "issue").order_by("-batch__shipment_date", "-batch_id")
        state = request.query_params.get("state")
        if state:
            live = Q(batch__rework_cases__origin="CUSTOMER_RETURN") & ~Q(batch__rework_cases__status="CANCELLED")
            if state == "NO_CLAIM":
                queryset = queryset.exclude(no_claim_reason="").exclude(live)
            elif state == "PENDING":
                from django.db.models import F
                queryset = queryset.filter(no_claim_reason="").exclude(live).annotate(
                    claimed=Sum("batch__lines__order_allocations__piece_quantity", default=0)
                ).filter(Q(batch__lines__piece_quantity__isnull=True) | Q(batch__lines__piece_quantity__gt=F("claimed")))
            else:
                raise ValidationError({"state": "无效补货状态。"})
        q = (request.query_params.get("q") or "").strip()
        if q:
            queryset = queryset.filter(
                Q(batch__shipment_no__icontains=q) | Q(batch__delivery_info__icontains=q)
                | Q(batch__specification_snapshot__iexact=q) | Q(customer_card_key=service.card_key(q))
                | Q(issue__origins__card_no_snapshot__iexact=q)
            ).distinct()
        paginator = QualityPagination()
        page = paginator.paginate_queryset(queryset, request)
        return paginator.get_paginated_response([service.item_data(item) for item in page])

    @quality_write_retry
    def post(self, request):
        _enabled()
        return Response(service.create_shipment(_payload(request), request.user), status=201)


class SupplementIssueView(APIView):
    def get(self, request, pk=None):
        _enabled()
        if pk:
            issue = service.row(ShipmentSupplementIssue.objects.all(), pk, "补货事项")
            return Response(service.issue_data(issue, include_shipments=True))
        paginator = QualityPagination()
        page = paginator.paginate_queryset(ShipmentSupplementIssue.objects.select_related("product").order_by("-pk"), request)
        return paginator.get_paginated_response([service.issue_data(issue) for issue in page])

    @quality_write_retry
    def post(self, request, pk=None):
        _enabled()
        data = _payload(request)
        return Response(service.update_issue(pk, data, request.user) if pk else
                        service.create_shipment(data, request.user, pending_only=True), status=200 if pk else 201)


class SupplementDetailView(APIView):
    def get(self, request, pk):
        _enabled()
        item = ShipmentSupplement.objects.select_related("batch__inspector", "issue").filter(batch_id=pk).first()
        if item is None:
            raise NotFound("找不到此无单出货。")
        return Response({**service.item_data(item, history=True), "issue": service.issue_data(item.issue)})

    @quality_write_retry
    def post(self, request, pk, operation=None):
        _enabled()
        action = {"claim": "CLAIM", "unclaim": "UNCLAIM", "no-claim": "NO_CLAIM",
                  "reopen": "REOPEN", "return": "RETURN", "verify-quantity": "VERIFY_QUANTITY",
                  "link-issue": "LINK_ISSUE"}.get(operation)
        if not action:
            raise NotFound("无此操作。")
        return Response(service.update_shipment(pk, action, _payload(request), request.user))


class SupplementLookupView(APIView):
    def get(self, request):
        _enabled()
        return Response(service.lookup_origins(request.query_params.get("code", "")))


class SupplementDeliveryReleaseView(APIView):
    @extend_schema(request=service.CommandSerializer, responses=dict)
    @quality_write_retry
    def post(self, request, pk):
        _enabled()
        return Response(service.release_delivery(pk, _payload(request), request.user))


class SupplementSearchView(APIView):
    def get(self, request):
        _enabled()
        args = service.CommandSerializer(data={"request_key": "00000000-0000-0000-0000-000000000000",
                                                "product_id": request.query_params.get("product_id"),
                                                "shipment_date": request.query_params.get("date")})
        args.is_valid(raise_exception=True)
        values = args.validated_data
        customer_code = (request.query_params.get("customer_code") or "").strip()
        project_no = (request.query_params.get("project_no") or "").strip()
        from orders.customer_inspection import get_order_snapshots
        from orders.identities import order_customer_key

        def matches(order):
            return bool(order and order_customer_key(order) == "翔邦"
                        and (not customer_code or service.identity(order.customer_code) == service.identity(customer_code))
                        and (not project_no or service.identity(get_order_snapshots([order]).get(order.pk, {}).get("project_no"))
                             == service.identity(project_no)))
        # Search suggestions only: never turn this product/date match into a binding.
        records = QualityShipmentBatch.objects.filter(
            Q(product_specification_id=values["product_id"]) | Q(lines__product_specification_id=values["product_id"]),
            status="CONFIRMED", shipment_date=values["shipment_date"],
        ).distinct().select_related("supplement__issue", "order__source_batch", "order__last_source_batch").prefetch_related(
            "lines__order__source_batch", "lines__order__last_source_batch", "lines__process_card__order__source_batch",
        ).order_by("id")
        rows = []
        for b in records:
            lines = list(b.lines.all())
            if b.supplement_kind:
                source = b.supplement.issue
                if (customer_code and service.identity(source.customer_code) != service.identity(customer_code)
                        or project_no and service.identity(source.project_no) != service.identity(project_no)):
                    continue
            elif not any(matches(order) for order in [b.order, *(line.order or (line.process_card.order if line.process_card_id else None) for line in lines)]):
                continue
            counts = [line.piece_quantity for line in lines]
            rows.append({"id": b.pk, "shipment_no": b.shipment_no, "date": b.shipment_date, "kind": b.supplement_kind,
                         "quantity": sum(counts) if counts and all(n is not None for n in counts) else None, "delivery_info": b.delivery_info})
        for b in QualityShipment.objects.filter(shipment_date=values["shipment_date"],
                                               order__product_specification_id=values["product_id"]).select_related(
                                                   "order__source_batch", "order__last_source_batch"):
            if matches(b.order):
                rows.append({"id": b.pk, "shipment_no": b.shipment_no, "date": b.shipment_date, "legacy": True,
                             "quantity": b.shipped_quantity, "delivery_info": ""})
        paginator = QualityPagination()
        return paginator.get_paginated_response(paginator.paginate_queryset(rows, request))


class SupplementOrdersView(APIView):
    def get(self, request, pk):
        _enabled()
        from .services import delivered_quantities_by_order
        item = ShipmentSupplement.objects.select_related("batch", "issue").filter(batch_id=pk).first()
        if not item:
            raise NotFound("找不到此无单出货。")
        term = (request.query_params.get("q") or "").strip()
        candidates = QualityOrder.objects.filter(
            product_specification_id=item.batch.product_specification_id,
        ).select_related("source_batch", "last_source_batch").order_by("item_no", "pk")
        if term:
            candidates = candidates.filter(Q(order_no__iexact=term)
                | Q(pk=term if term.isascii() and term.isdigit() and len(term) < 10 else 0))
        else:
            adjusted = service.effective_supplement_adjustments().filter(origin__issue_id=item.issue_id)
            candidates = candidates.filter(pk__in=adjusted.values("allocation__order_id"))
        rows = []
        for order in candidates[:100]:
            try:
                service._active_order(order, item)
            except ValidationError:
                continue
            rows.append(order)
        totals = delivered_quantities_by_order([o.pk for o in rows])
        return Response([{"id": o.pk, "order_no": o.order_no, "item_no": o.item_no, "customer_code": o.customer_code,
                          "remaining": max(0, o.order_quantity - totals[o.pk])} for o in rows])
