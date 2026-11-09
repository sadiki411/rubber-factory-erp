from functools import wraps
import random
import time

from django.db import IntegrityError, OperationalError, connection, transaction
from django.db.models import DecimalField, OuterRef, Q, Subquery, Sum
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema

from erp.pagination import PublicLocationHistoryPagination

from .models import (
    InventoryBatch,
    InventoryContainer,
    InventoryLocation,
    InventoryOutbound,
    InventoryProduct,
    InventoryTransaction,
    InventoryTopUp,
    MaterialRemainder,
    MaterialRemainderUse,
)
from quality.models import ProductUnitWeight
from .serializers import (
    InventoryBatchSerializer,
    InventoryContainerSerializer,
    InventoryLocationSerializer,
    InventoryMoveSerializer,
    InventoryOutboundReadSerializer,
    InventoryOutboundSerializer,
    InventoryProductCorrectionSerializer,
    InventoryProductSerializer,
    InventoryQualityUpdateSerializer,
    InventoryReceiptSerializer,
    InventoryTransactionSerializer,
    InventoryTopUpInputSerializer,
    InventoryTopUpReadSerializer,
    InventoryTopUpResponseSerializer,
    MaterialRemainderSerializer,
    MaterialRemainderUseSerializer,
)
from .services import (
    bootstrap_inventory_locations,
    correct_inventory_container_product,
    move_inventory_container,
    product_availability,
    set_inventory_container_quality,
    top_up_inventory_container,
    InventoryTopUpConflict,
)


class InventoryWriteBusy(APIException):
    status_code = 503
    default_detail = "库存正在被其他操作更新，本次未入账；请稍后重试，同一次提交不会重复增加。"


class InventoryWriteConflict(APIException):
    status_code = 409


def inventory_receipt_retry(operation):
    @wraps(operation)
    def wrapped(self, request, *args, **kwargs):
        nested = connection.in_atomic_block
        for attempt in range(4):
            try:
                with transaction.atomic():
                    return operation(self, request, *args, **kwargs)
            except OperationalError as exc:
                if connection.vendor != "sqlite" or not any(text in str(exc).lower() for text in ("database is locked", "table is locked")):
                    raise
                if nested or attempt == 3:
                    raise InventoryWriteBusy() from exc
                time.sleep(random.uniform(0.025, 0.05) * (2 ** attempt))
            except IntegrityError:
                # A concurrent identical UUID may win on another container.
                # Retry only after the whole failed transaction rolled back.
                if nested or attempt == 3 or not InventoryTopUp.objects.filter(request_id=request.data.get("request_id")).exists():
                    raise
        raise InventoryWriteBusy()
    return wrapped


class InventoryPagination(PageNumberPagination):
    """Keep inventory lists bounded while allowing complete selector datasets."""

    page_size = 30
    page_size_query_param = "page_size"
    max_page_size = 1000


class InventoryLocationViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = InventoryLocation.objects.all().prefetch_related("inventory_container__batch__product", "inventory_container__batch__inspector")
    serializer_class = InventoryLocationSerializer
    # Fixed locations are selector master data. Returning only the global first
    # page silently hides most of the 150 physical shelf positions.
    pagination_class = None

    def get_queryset(self):
        queryset = super().get_queryset()
        rack = str(self.request.query_params.get("rack", "")).strip()
        if rack:
            queryset = queryset.filter(rack_code__iexact=rack)
        if self.request.query_params.get("active") in {"1", "true", "True"}:
            queryset = queryset.filter(is_active=True)
        return queryset

    @action(detail=False, methods=["post"])
    def bootstrap(self, request):
        created = bootstrap_inventory_locations()
        return Response({"created": created, "locations": self.get_queryset().count()})


class PublicInventoryLocationView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(responses=dict)
    def get(self, request, code):
        location = get_object_or_404(
            InventoryLocation.objects.select_related("inventory_container__batch__product"),
            code__iexact=code,
        )
        container = location.current_container
        product = container.batch.product if container else None
        # Never reuse the authenticated serializer here: it contains employee
        # names and may grow additional internal fields in future.
        return Response({
            "id": location.pk,
            "code": location.code,
            "label": location.label,
            "is_active": location.is_active,
            "container": {
                "container_code": container.container_code,
                "container_type_label": container.get_container_type_display(),
                "batch_no": container.batch.batch_no,
                "product_code": product.product_code,
                "product_name": product.product_name,
                "specification": product.specification,
                "material": product.material,
                "quantity": container.quantity,
                "quality_status_label": container.batch.get_quality_status_display(),
                "bag_count": container.bag_count,
                "pieces_per_bag": container.pieces_per_bag,
            } if container else None,
        })


class PublicInventoryLocationHistoryView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(responses=dict)
    def get(self, request, code):
        location = get_object_or_404(InventoryLocation, code__iexact=code)
        transactions = InventoryTransaction.objects.filter(
            Q(from_location=location) | Q(to_location=location)
        ).select_related(
            "batch__product", "container", "from_location", "to_location", "top_up",
        ).order_by("-created_at", "-id")
        paginator = PublicLocationHistoryPagination()
        page = paginator.paginate_queryset(transactions, request, view=self)
        return paginator.get_paginated_response([
            {
                "id": entry.pk,
                "created_at": entry.created_at,
                "operation_label": "追加入库" if hasattr(entry, "top_up") else "产品资料更正" if entry.transaction_type == InventoryTransaction.TransactionType.ADJUST
                and entry.quantity == 0 and entry.reason.startswith("库存产品资料更正：")
                else entry.get_transaction_type_display(),
                "item_code": entry.top_up.product_snapshot.get("product_code", "") if hasattr(entry, "top_up") else entry.batch.product.product_code,
                "item_name": entry.top_up.product_snapshot.get("product_name", "") if hasattr(entry, "top_up") else entry.batch.product.product_name,
                "specification": entry.top_up.product_snapshot.get("specification", "") if hasattr(entry, "top_up") else entry.batch.product.specification,
                "material": entry.top_up.product_snapshot.get("material", "") if hasattr(entry, "top_up") else entry.batch.product.material,
                "batch_no": entry.batch.batch_no,
                "container_code": entry.container.container_code,
                "quantity_change": -entry.quantity if entry.transaction_type == InventoryTransaction.TransactionType.OUTBOUND
                else entry.quantity if entry.transaction_type in {
                    InventoryTransaction.TransactionType.RECEIPT, InventoryTransaction.TransactionType.ADJUST,
                } else None,
                "from_location": entry.from_location.code if entry.from_location_id else None,
                "to_location": entry.to_location.code if entry.to_location_id else None,
            }
            for entry in page
        ])


class InventoryProductViewSet(viewsets.ModelViewSet):
    latest_quality_weight = ProductUnitWeight.objects.filter(
        product_specification_id=OuterRef("product_specification_id"),
        is_active=True,
        unit_weight_g__gt=0,
    ).order_by("-created_at", "-id").values("unit_weight_g")[:1]
    queryset = InventoryProduct.objects.all().select_related("product_specification").annotate(
        latest_quality_unit_weight_g=Subquery(
            latest_quality_weight,
            output_field=DecimalField(max_digits=14, decimal_places=5),
        )
    )
    serializer_class = InventoryProductSerializer
    pagination_class = InventoryPagination
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        queryset = super().get_queryset()
        query = str(self.request.query_params.get("q", "")).strip()
        if query:
            queryset = queryset.filter(
                Q(product_code__icontains=query)
                | Q(product_name__icontains=query)
                | Q(specification__icontains=query)
                | Q(material__icontains=query)
            )
        return queryset


class InventoryBatchViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = InventoryBatch.objects.all().select_related("product", "inspector").prefetch_related("containers")
    serializer_class = InventoryBatchSerializer
    pagination_class = InventoryPagination

    def get_queryset(self):
        queryset = super().get_queryset()
        quality_status = self.request.query_params.get("quality_status")
        query = str(self.request.query_params.get("q", "")).strip()
        if quality_status:
            queryset = queryset.filter(quality_status=quality_status)
        if query:
            queryset = queryset.filter(
                Q(batch_no__icontains=query)
                | Q(product__product_code__icontains=query)
                | Q(product__product_name__icontains=query)
                | Q(product__specification__icontains=query)
                | Q(product__material__icontains=query)
            )
        return queryset


class InventoryContainerViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = InventoryContainer.objects.all().select_related("batch__product", "batch__inspector", "location")
    serializer_class = InventoryContainerSerializer
    pagination_class = InventoryPagination

    def get_queryset(self):
        queryset = super().get_queryset()
        quality_status = self.request.query_params.get("quality_status")
        if quality_status:
            queryset = queryset.filter(batch__quality_status=quality_status)
        if self.request.query_params.get("active") in {"1", "true", "True"}:
            queryset = queryset.filter(status=InventoryContainer.Status.ACTIVE)
        return queryset

    @extend_schema(request=InventoryTopUpInputSerializer, responses=InventoryTopUpResponseSerializer)
    @action(detail=True, methods=["post"], url_path="top-up")
    @inventory_receipt_retry
    def top_up(self, request, pk=None):
        self.get_object()
        serializer = InventoryTopUpInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            container, receipt, replayed = top_up_inventory_container(int(pk), serializer.validated_data, created_by=request.user)
        except InventoryTopUpConflict as exc:
            raise InventoryWriteConflict(str(exc)) from exc
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        data = self.get_serializer(self.get_queryset().get(pk=container.pk)).data
        data["top_up_receipt"] = InventoryTopUpReadSerializer(receipt).data
        data["replayed"] = replayed
        return Response(data)

    @extend_schema(responses=InventoryTopUpReadSerializer(many=True))
    @action(detail=True, methods=["get"], url_path="top-up-history")
    def top_up_history(self, request, pk=None):
        self.get_object()
        rows = InventoryTopUp.objects.filter(transaction__container_id=pk).select_related("transaction__created_by")
        page = self.paginate_queryset(rows)
        return self.get_paginated_response(InventoryTopUpReadSerializer(page, many=True).data)

    @action(detail=True, methods=["post"])
    def move(self, request, pk=None):
        container = self.get_object()
        serializer = InventoryMoveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        location_id = serializer.validated_data["location_id"]
        try:
            target = InventoryLocation.objects.get(pk=location_id)
            move_inventory_container(container, target, created_by=request.user, reason=serializer.validated_data.get("reason", ""))
        except (InventoryLocation.DoesNotExist, ValueError) as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(self.get_serializer(InventoryContainer.objects.select_related("batch__product", "location").get(pk=container.pk)).data)

    @action(detail=True, methods=["post"], url_path="set-quality")
    def set_quality(self, request, pk=None):
        container = self.get_object()
        serializer = InventoryQualityUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            set_inventory_container_quality(container, serializer.validated_data, created_by=request.user)
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(self.get_serializer(self.get_queryset().get(pk=container.pk)).data)

    @action(detail=True, methods=["post"], url_path="correct-product")
    def correct_product(self, request, pk=None):
        serializer = InventoryProductCorrectionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            correct_inventory_container_product(
                self.get_object(), serializer.validated_data, created_by=request.user,
            )
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        refreshed = self.get_queryset().get(pk=pk)
        return Response(self.get_serializer(refreshed).data)


class InventoryReceiptView(APIView):
    def post(self, request):
        serializer = InventoryReceiptSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        try:
            container = serializer.save()
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(InventoryContainerSerializer(container).data, status=status.HTTP_201_CREATED)


class InventoryOutboundViewSet(viewsets.ModelViewSet):
    queryset = InventoryOutbound.objects.all().select_related("order", "created_by").prefetch_related("lines__container__batch__product")
    pagination_class = InventoryPagination
    http_method_names = ["get", "post", "head", "options"]

    def get_serializer_class(self):
        return InventoryOutboundReadSerializer if self.request.method in {"GET", "HEAD", "OPTIONS"} else InventoryOutboundSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        try:
            outbound = serializer.save()
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(InventoryOutboundReadSerializer(outbound).data, status=status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        raise ValidationError({"detail": "库存出库单不可直接删除，请使用冲销流程。"})


class InventoryTransactionViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = InventoryTransaction.objects.all().select_related("batch", "container", "from_location", "to_location", "outbound", "created_by")
    serializer_class = InventoryTransactionSerializer
    pagination_class = InventoryPagination


class MaterialRemainderViewSet(viewsets.ModelViewSet):
    queryset = MaterialRemainder.objects.all().select_related("created_by").prefetch_related("uses")
    serializer_class = MaterialRemainderSerializer
    pagination_class = InventoryPagination
    http_method_names = ["get", "post", "head", "options"]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save()

    @action(detail=True, methods=["post"], url_path="use")
    def use(self, request, pk=None):
        remainder = self.get_object()
        serializer = MaterialRemainderUseSerializer(data={**request.data, "remainder": remainder.pk})
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            locked_remainder = MaterialRemainder.objects.select_for_update().get(pk=remainder.pk)
            used = locked_remainder.uses.aggregate(total=Sum("weight_kg"))["total"] or 0
            current = locked_remainder.weight_kg - used
            if serializer.validated_data["weight_kg"] > current:
                raise ValidationError({"weight_kg": f"剩余胶料只有 {current} kg。"})
            usage = serializer.save(created_by=request.user)
        return Response(MaterialRemainderUseSerializer(usage).data, status=status.HTTP_201_CREATED)


class InventorySummaryView(APIView):
    def get(self, request):
        active = InventoryContainer.objects.filter(status=InventoryContainer.Status.ACTIVE)
        total = active.aggregate(total=Sum("quantity"))["total"] or 0
        available = active.filter(batch__quality_status=InventoryBatch.QualityStatus.PASSED).aggregate(total=Sum("quantity"))["total"] or 0
        waiting = active.filter(batch__quality_status=InventoryBatch.QualityStatus.WAITING).aggregate(total=Sum("quantity"))["total"] or 0
        failed = active.filter(batch__quality_status=InventoryBatch.QualityStatus.FAILED).aggregate(total=Sum("quantity"))["total"] or 0
        held = active.filter(batch__quality_status=InventoryBatch.QualityStatus.HOLD).aggregate(total=Sum("quantity"))["total"] or 0
        return Response({
            "total_quantity": total,
            "available_quantity": available,
            "waiting_inspection_quantity": waiting,
            "failed_quantity": failed,
            "held_quantity": held,
            "active_containers": active.count(),
            "occupied_locations": active.filter(location__isnull=False, location__rack_type__in=[InventoryLocation.RackType.LARGE, InventoryLocation.RackType.SMALL]).count(),
            "location_count": InventoryLocation.objects.filter(is_active=True, rack_type__in=[InventoryLocation.RackType.LARGE, InventoryLocation.RackType.SMALL]).count(),
        })


class InventoryAvailabilityView(APIView):
    def get(self, request):
        product_specification_id = request.query_params.get("product_specification_id")
        return Response(product_availability(
            product_specification_id=int(product_specification_id) if product_specification_id else None,
            product_code=request.query_params.get("product_code", ""),
            specification=request.query_params.get("specification", ""),
            material=request.query_params.get("material", ""),
        ))
