from decimal import Decimal
from uuid import UUID
from zipfile import BadZipFile

from django.db.models import DecimalField, ExpressionWrapper, F, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from openpyxl.utils.exceptions import InvalidFileException
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from quality.models import QualityOrder

from .imports import (
    commit_business_batch,
    create_business_error_report,
    create_business_template,
    preview_business_workbook,
)
from .models import (
    BusinessImportBatch,
    BusinessRecordRevision,
    MaterialReceipt,
    ProductInspectionCriterion,
    ProductSpecification,
)
from .serializers import (
    BusinessOrderSerializer,
    BusinessRecordRevisionSerializer,
    MaterialReceiptSerializer,
    ProductInspectionCriterionSerializer,
    ProductSpecificationSerializer,
)


class BusinessPagination(PageNumberPagination):
    page_size = 30
    page_size_query_param = "page_size"
    max_page_size = 1000


class NoDeleteModelViewSet(viewsets.ModelViewSet):
    pagination_class = BusinessPagination
    http_method_names = ["get", "post", "put", "patch", "head", "options"]


class RevisionHistoryMixin:
    revision_record_type = None

    @action(detail=True, methods=["get"])
    def history(self, request, pk=None):
        self.get_object()
        revisions = BusinessRecordRevision.objects.filter(
            record_type=self.revision_record_type, record_id=pk
        ).select_related("operator")
        return Response(BusinessRecordRevisionSerializer(revisions, many=True).data)


class ProductSpecificationViewSet(RevisionHistoryMixin, NoDeleteModelViewSet):
    serializer_class = ProductSpecificationSerializer
    revision_record_type = BusinessRecordRevision.RecordType.PRODUCT_SPECIFICATION

    def get_queryset(self):
        queryset = ProductSpecification.objects.all()
        params = self.request.query_params
        q = str(params.get("q", "") or "").strip()
        if q:
            queryset = queryset.filter(
                Q(product_name__icontains=q)
                | Q(customer_product_no__icontains=q)
                | Q(specification__icontains=q)
                | Q(material__icontains=q)
                | Q(mold_no__icontains=q)
                | Q(mold_size__icontains=q)
                | Q(notes__icontains=q)
            )
        active = str(params.get("active", params.get("is_active", ""))).strip().lower()
        if active in {"1", "true", "yes"}:
            queryset = queryset.filter(is_active=True)
        elif active in {"0", "false", "no"}:
            queryset = queryset.filter(is_active=False)
        material = str(params.get("material", "") or "").strip()
        if material:
            queryset = queryset.filter(material__icontains=material)
        return queryset.order_by("specification", "material", "id")


def _business_order_queryset():
    decimal_field = DecimalField(max_digits=18, decimal_places=3)
    imported = Coalesce(
        Sum("material_receipts__weight_kg"), Value(Decimal("0")), output_field=decimal_field
    )
    manual = Coalesce(
        F("manual_received_material_kg"), Value(Decimal("0")), output_field=decimal_field
    )
    return (
        QualityOrder.objects.select_related("product_specification", "created_by")
        .annotate(imported_received_material_kg_value=imported)
        .annotate(
            received_material_kg_value=ExpressionWrapper(
                F("imported_received_material_kg_value") + manual,
                output_field=decimal_field,
            )
        )
    )


class BusinessOrderViewSet(RevisionHistoryMixin, NoDeleteModelViewSet):
    serializer_class = BusinessOrderSerializer
    revision_record_type = BusinessRecordRevision.RecordType.ORDER

    def get_queryset(self):
        queryset = _business_order_queryset()
        params = self.request.query_params
        q = str(params.get("q", "") or "").strip()
        if q:
            queryset = queryset.filter(
                Q(order_no__icontains=q)
                | Q(item_no__icontains=q)
                | Q(batch_no__icontains=q)
                | Q(product_code__icontains=q)
                | Q(product_name__icontains=q)
                | Q(specification__icontains=q)
                | Q(material__icontains=q)
                | Q(product_specification__customer_product_no__icontains=q)
            )
        status_value = str(params.get("status", "") or "").strip().upper()
        if status_value:
            if status_value not in QualityOrder.Status.values:
                raise DRFValidationError({"status": "无效的订单状态。"})
            queryset = queryset.filter(status=status_value)
        production_required = str(params.get("production_required", "") or "").strip().lower()
        if production_required in {"1", "true", "yes"}:
            queryset = queryset.filter(production_required=True)
        elif production_required in {"0", "false", "no"}:
            queryset = queryset.filter(production_required=False)
        material_status = str(params.get("material_status", "") or "").strip().upper()
        if material_status:
            if material_status == "UNKNOWN":
                queryset = queryset.filter(required_material_kg__isnull=True)
            elif material_status == "NOT_RECEIVED":
                queryset = queryset.filter(
                    required_material_kg__isnull=False,
                    received_material_kg_value__lte=0,
                )
            elif material_status == "PARTIAL":
                queryset = queryset.filter(
                    required_material_kg__isnull=False,
                    received_material_kg_value__gt=0,
                    received_material_kg_value__lt=F("required_material_kg"),
                )
            elif material_status == "SUFFICIENT":
                queryset = queryset.filter(
                    required_material_kg__isnull=False,
                    received_material_kg_value=F("required_material_kg"),
                )
            elif material_status == "OVER":
                queryset = queryset.filter(
                    required_material_kg__isnull=False,
                    received_material_kg_value__gt=F("required_material_kg"),
                )
            else:
                raise DRFValidationError({"material_status": "无效的胶料状态。"})
        date_from = str(params.get("date_from", "") or "").strip()
        date_to = str(params.get("date_to", "") or "").strip()
        if date_from:
            queryset = queryset.filter(order_date__gte=date_from)
        if date_to:
            queryset = queryset.filter(order_date__lte=date_to)
        ordering = str(params.get("ordering", "") or "").strip()
        allowed = {
            "order_date",
            "-order_date",
            "due_date",
            "-due_date",
            "order_no",
            "-order_no",
            "created_at",
            "-created_at",
        }
        return queryset.order_by(ordering if ordering in allowed else "-order_date", "-id")

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class MaterialReceiptViewSet(RevisionHistoryMixin, NoDeleteModelViewSet):
    serializer_class = MaterialReceiptSerializer
    revision_record_type = BusinessRecordRevision.RecordType.MATERIAL_RECEIPT

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "linked",
                bool,
                description="true仅返回已关联订单的收料，false仅返回未关联收料",
            )
        ]
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def get_queryset(self):
        queryset = MaterialReceipt.objects.select_related("order")
        params = self.request.query_params
        q = str(params.get("q", "") or "").strip()
        if q:
            queryset = queryset.filter(
                Q(order_no__icontains=q)
                | Q(item_no__icontains=q)
                | Q(finished_product_name__icontains=q)
                | Q(specification__icontains=q)
                | Q(material__icontains=q)
                | Q(batch_no__icontains=q)
            )
        order_id = str(params.get("order_id", "") or "").strip()
        if order_id:
            if not order_id.isdigit():
                raise DRFValidationError({"order_id": "订单ID必须是整数。"})
            queryset = queryset.filter(order_id=int(order_id))
        order_no = str(params.get("order_no", "") or "").strip()
        if order_no:
            queryset = queryset.filter(order_no__icontains=order_no)
        batch_no = str(params.get("batch_no", "") or "").strip()
        if batch_no:
            queryset = queryset.filter(batch_no__icontains=batch_no)
        linked = str(params.get("linked", "") or "").strip().lower()
        if linked in {"1", "true", "yes"}:
            queryset = queryset.filter(order__isnull=False)
        elif linked in {"0", "false", "no"}:
            queryset = queryset.filter(order__isnull=True)
        elif linked:
            raise DRFValidationError({"linked": "linked必须为true或false。"})
        date_from = str(params.get("date_from", "") or "").strip()
        date_to = str(params.get("date_to", "") or "").strip()
        if date_from:
            queryset = queryset.filter(manufactured_on__gte=date_from)
        if date_to:
            queryset = queryset.filter(manufactured_on__lte=date_to)
        return queryset.order_by("-manufactured_on", "-id")


class ProductInspectionCriterionViewSet(RevisionHistoryMixin, NoDeleteModelViewSet):
    serializer_class = ProductInspectionCriterionSerializer
    revision_record_type = BusinessRecordRevision.RecordType.INSPECTION_CRITERION

    def get_queryset(self):
        queryset = ProductInspectionCriterion.objects.select_related(
            "product_specification", "order"
        )
        q = str(self.request.query_params.get("q", "") or "").strip()
        if q:
            queryset = queryset.filter(
                Q(project_no__icontains=q)
                | Q(customer__icontains=q)
                | Q(category__icontains=q)
                | Q(inspection_item__icontains=q)
                | Q(product_specification__specification__icontains=q)
            )
        product_id = str(
            self.request.query_params.get("product_specification_id", "") or ""
        ).strip()
        if product_id:
            if not product_id.isdigit():
                raise DRFValidationError(
                    {"product_specification_id": "产品规格ID必须是整数。"}
                )
            queryset = queryset.filter(product_specification_id=int(product_id))
        return queryset.order_by("product_specification_id", "category", "inspection_item", "id")


class BusinessImportTemplateView(APIView):
    @extend_schema(
        parameters=[OpenApiParameter("type", str, description="product_specifications/orders/material_receipts")],
        responses={(200, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"): bytes},
    )
    def get(self, request):
        kind = str(request.query_params.get("type", "product_specifications") or "").strip()
        try:
            content = create_business_template(kind)
        except ValueError as exc:
            raise DRFValidationError({"type": str(exc)}) from exc
        response = HttpResponse(
            content,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = (
            f"attachment; filename=business-{kind}-template.xlsx"
        )
        return response


class BusinessImportPreviewView(APIView):
    @extend_schema(request={"multipart/form-data": dict}, responses=dict)
    def post(self, request):
        uploaded_file = request.FILES.get("file")
        if uploaded_file is None:
            raise DRFValidationError({"file": "请选择Excel文件。"})
        if not uploaded_file.name.lower().endswith(".xlsx"):
            raise DRFValidationError({"file": "仅支持.xlsx文件。"})
        try:
            result = preview_business_workbook(uploaded_file, request.user)
        except (ValueError, KeyError, OSError, BadZipFile, InvalidFileException) as exc:
            raise DRFValidationError({"file": str(exc)}) from exc
        return Response(result)


class BusinessImportCommitView(APIView):
    @extend_schema(request=dict, responses=dict)
    def post(self, request):
        token = str(request.data.get("token", "") or "").strip()
        try:
            batch_id = UUID(token)
        except (ValueError, TypeError, AttributeError) as exc:
            raise DRFValidationError({"token": "无效的业务导入批次标识。"}) from exc
        batch = get_object_or_404(BusinessImportBatch, pk=batch_id, created_by=request.user)
        try:
            result = commit_business_batch(batch, request.user)
        except ValueError as exc:
            raise DRFValidationError({"detail": str(exc)}) from exc
        return Response(result)


class BusinessImportErrorReportView(APIView):
    @extend_schema(
        responses={(200, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"): bytes}
    )
    def get(self, request, token):
        batch = get_object_or_404(BusinessImportBatch, pk=token, created_by=request.user)
        response = HttpResponse(
            create_business_error_report(batch),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f"attachment; filename=business-import-errors-{batch.pk}.xlsx"
        return response
