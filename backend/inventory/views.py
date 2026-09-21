from django.db import transaction
from django.db.models import Q, Sum
from django.utils.dateparse import parse_date
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    InventoryBatch,
    InventoryContainer,
    InventoryLocation,
    InventoryOutbound,
    InventoryProduct,
    InventoryTransaction,
    MaterialRemainder,
    MaterialRemainderUse,
)
from quality.models import QualityEmployee
from .serializers import (
    InventoryBatchSerializer,
    InventoryContainerSerializer,
    InventoryLocationSerializer,
    InventoryOutboundReadSerializer,
    InventoryOutboundSerializer,
    InventoryProductSerializer,
    InventoryReceiptSerializer,
    InventoryTransactionSerializer,
    MaterialRemainderSerializer,
    MaterialRemainderUseSerializer,
)
from .services import bootstrap_inventory_locations, move_inventory_container, product_availability


class InventoryLocationViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = InventoryLocation.objects.all().prefetch_related("inventory_container__batch__product", "inventory_container__batch__inspector")
    serializer_class = InventoryLocationSerializer

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


class InventoryProductViewSet(viewsets.ModelViewSet):
    queryset = InventoryProduct.objects.all().select_related("product_specification")
    serializer_class = InventoryProductSerializer
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

    def get_queryset(self):
        queryset = super().get_queryset()
        quality_status = self.request.query_params.get("quality_status")
        if quality_status:
            queryset = queryset.filter(batch__quality_status=quality_status)
        if self.request.query_params.get("active") in {"1", "true", "True"}:
            queryset = queryset.filter(status=InventoryContainer.Status.ACTIVE)
        return queryset

    @action(detail=True, methods=["post"])
    def move(self, request, pk=None):
        container = self.get_object()
        location_id = request.data.get("location_id")
        if not location_id:
            raise ValidationError({"location_id": "请选择目标库位。"})
        try:
            target = InventoryLocation.objects.get(pk=location_id)
            move_inventory_container(container, target, created_by=request.user, reason=request.data.get("reason", ""))
        except (InventoryLocation.DoesNotExist, ValueError) as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(self.get_serializer(InventoryContainer.objects.select_related("batch__product", "location").get(pk=container.pk)).data)

    @action(detail=True, methods=["post"], url_path="set-quality")
    def set_quality(self, request, pk=None):
        container = self.get_object()
        status_value = request.data.get("quality_status")
        if status_value not in InventoryBatch.QualityStatus.values:
            raise ValidationError({"quality_status": "无效的质量状态。"})
        if status_value == InventoryBatch.QualityStatus.PASSED and not request.data.get("inspector_id"):
            raise ValidationError({"inspector_id": "已检合格必须选择品检员。"})
        inspector_id = request.data.get("inspector_id")
        if inspector_id and not QualityEmployee.objects.filter(pk=inspector_id, is_active=True).exists():
            raise ValidationError({"inspector_id": "品检员不存在或已停用。"})
        with transaction.atomic():
            batch = container.batch
            batch.quality_status = status_value
            batch.inspector_id = inspector_id or None
            inspected_on = request.data.get("inspected_on") or None
            if inspected_on and parse_date(str(inspected_on)) is None:
                raise ValidationError({"inspected_on": "日期格式应为yyyy-mm-dd。"})
            batch.inspected_on = parse_date(str(inspected_on)) if inspected_on else None
            batch.save(update_fields=["quality_status", "inspector", "inspected_on", "updated_at"])
            InventoryTransaction.objects.create(
                transaction_type=InventoryTransaction.TransactionType.QUALITY,
                batch=batch,
                container=container,
                quantity=0,
                from_location=container.location,
                to_location=container.location,
                reason=f"质量状态变更为 {batch.get_quality_status_display()}",
                created_by=request.user,
            )
        return Response(self.get_serializer(container).data)


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


class MaterialRemainderViewSet(viewsets.ModelViewSet):
    queryset = MaterialRemainder.objects.all().select_related("created_by").prefetch_related("uses")
    serializer_class = MaterialRemainderSerializer
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
        return Response({
            "total_quantity": total,
            "available_quantity": available,
            "waiting_inspection_quantity": waiting,
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
