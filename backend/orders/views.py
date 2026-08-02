import hashlib
import logging
import re
from uuid import UUID
from zipfile import BadZipFile

from django.core.files.base import ContentFile
from django.db.models import F, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
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
from .services import with_order_activity


logger = logging.getLogger(__name__)
SENSITIVE_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]|(?:^|\s)/(?:home|users|tmp|var|app)/)",
    re.IGNORECASE,
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
        queryset = ProductSpecification.objects.select_related("mold_model")
        params = self.request.query_params
        q = str(params.get("q", "") or "").strip()
        if q:
            queryset = queryset.filter(
                Q(product_name__icontains=q)
                | Q(customer_product_no__icontains=q)
                | Q(specification__icontains=q)
                | Q(material__icontains=q)
                | Q(mold_model__code__icontains=q)
                | Q(mold_model__product_name__icontains=q)
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
    return with_order_activity(
        QualityOrder.objects.select_related(
            "product_specification",
            "product_specification__mold_model",
            "created_by",
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
        date_ordering = {
            "order_date": F("order_date").asc(nulls_last=True),
            "-order_date": F("order_date").desc(nulls_last=True),
            "due_date": F("due_date").asc(nulls_last=True),
            "-due_date": F("due_date").desc(nulls_last=True),
        }
        plain_ordering = {"order_no", "-order_no", "created_at", "-created_at"}
        if ordering in date_ordering:
            primary_ordering = date_ordering[ordering]
        elif ordering in plain_ordering:
            primary_ordering = ordering
        else:
            primary_ordering = date_ordering["-order_date"]
        return queryset.order_by(primary_ordering, "-id")

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
            queryset = queryset.filter(issued_on__gte=date_from)
        if date_to:
            queryset = queryset.filter(issued_on__lte=date_to)
        ordering = str(params.get("ordering", "") or "").strip()
        date_ordering = {
            "issued_on": F("issued_on").asc(nulls_last=True),
            "-issued_on": F("issued_on").desc(nulls_last=True),
            "manufactured_on": F("manufactured_on").asc(nulls_last=True),
            "-manufactured_on": F("manufactured_on").desc(nulls_last=True),
        }
        primary_ordering = date_ordering.get(
            ordering,
            date_ordering["-issued_on"],
        )
        secondary_ordering = (
            F("issued_on").desc(nulls_last=True)
            if "manufactured_on" in ordering
            else F("manufactured_on").desc(nulls_last=True)
        )
        return queryset.order_by(
            primary_ordering,
            secondary_ordering,
            "-id",
        )


class ProductInspectionCriterionViewSet(RevisionHistoryMixin, NoDeleteModelViewSet):
    serializer_class = ProductInspectionCriterionSerializer
    revision_record_type = BusinessRecordRevision.RecordType.INSPECTION_CRITERION

    def get_queryset(self):
        queryset = ProductInspectionCriterion.objects.select_related(
            "product_specification",
            "product_specification__mold_model",
            "order",
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
        except (
            ValueError,
            KeyError,
            OSError,
            BadZipFile,
            InvalidFileException,
            SyntaxError,
        ) as exc:
            message = _safe_preview_failure_message(exc)
            try:
                _record_failed_import_preview(uploaded_file, request.user, message)
            except Exception:
                logger.exception("Failed to persist an Excel preview failure")
            raise DRFValidationError({"file": message}) from exc
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
            message = _safe_business_message(exc, "导入提交失败，请重新预检后再试。")
            try:
                _record_failed_import_commit(batch, message)
            except Exception:
                logger.exception("Failed to persist an Excel commit failure")
            raise DRFValidationError({"detail": message}) from exc
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


def _import_issue(level, message, *, stage, **extra):
    return {
        "level": level,
        "message": str(message),
        "stage": stage,
        "recorded_at": timezone.now().isoformat(),
        **extra,
    }


def _safe_business_message(exc, fallback):
    message = str(exc or "").strip()
    if not message or SENSITIVE_PATH_RE.search(message) or "traceback" in message.casefold():
        return fallback
    return " ".join(message.split())[:500]


def _safe_preview_failure_message(exc):
    if isinstance(exc, ValueError):
        return _safe_business_message(exc, "Excel文件无法读取或格式不受支持。")
    return "Excel文件无法读取或格式不受支持。"


def _record_failed_import_preview(uploaded_file, user, message):
    """Persist files that cannot be parsed so failures remain auditable."""
    uploaded_file.seek(0)
    data = uploaded_file.read()
    uploaded_file.seek(0)
    name = str(getattr(uploaded_file, "name", "upload.xlsx") or "upload.xlsx")[:255]
    issue = _import_issue("error", message, stage="preview", field="file")
    batch = BusinessImportBatch(
        source_type=getattr(BusinessImportBatch.SourceType, "UNKNOWN", "UNKNOWN"),
        parser="unrecognized",
        status=BusinessImportBatch.Status.FAILED,
        original_name=name,
        sha256=hashlib.sha256(data).hexdigest(),
        payload={"rows": [], "parser": "unrecognized"},
        errors=[issue],
        warnings=[],
        created_by=user,
    )
    batch.original_file.save(name, ContentFile(data), save=False)
    batch.save()


def _record_failed_import_commit(batch, message):
    """Mark a real commit failure without changing blocked or completed previews."""
    current = BusinessImportBatch.objects.filter(pk=batch.pk).first()
    if (
        current is None
        or current.status != BusinessImportBatch.Status.PREVIEWED
        or current.errors
    ):
        return
    errors = [item for item in (current.errors or []) if isinstance(item, dict)]
    errors.append(_import_issue("error", message, stage="commit"))
    BusinessImportBatch.objects.filter(
        pk=current.pk, status=BusinessImportBatch.Status.PREVIEWED
    ).update(status=BusinessImportBatch.Status.FAILED, errors=errors)


def _batch_rows(batch):
    rows = batch.payload.get("rows", []) if isinstance(batch.payload, dict) else []
    return rows if isinstance(rows, list) else []


def _issue_list(value):
    return [item for item in (value or []) if isinstance(item, dict)]


def _batch_counts(batch):
    rows = _batch_rows(batch)
    record_keys = {
        "PRODUCT_SPECIFICATION": "product_specifications",
        "ORDER": "orders",
        "MATERIAL_RECEIPT": "material_receipts",
        "INSPECTION_CRITERION": "inspection_criteria",
    }
    records = {key: 0 for key in record_keys.values()}
    actions = {"CREATE": 0, "UPDATE": 0, "SKIP": 0}
    for row in rows:
        if not isinstance(row, dict):
            continue
        record_key = record_keys.get(str(row.get("record_type", "")))
        if record_key:
            records[record_key] += 1
        action_name = str(row.get("action", ""))
        if action_name in actions:
            actions[action_name] += 1
    return records, actions


def _batch_summary(batch):
    records, actions = _batch_counts(batch)
    errors = _issue_list(batch.errors)
    warnings = _issue_list(batch.warnings)
    display_status = batch.get_status_display()
    if batch.status == BusinessImportBatch.Status.PREVIEWED and errors:
        display_status = "预检未通过"
    return {
        "token": str(batch.pk),
        "original_name": batch.original_name,
        "source_type": batch.source_type,
        "source_type_display": batch.get_source_type_display(),
        "parser": batch.parser,
        "status": batch.status,
        "status_display": display_status,
        "created_at": batch.created_at,
        "committed_at": batch.committed_at,
        "total_rows": sum(records.values()),
        "counts": records,
        "actions": actions,
        "error_count": len(errors),
        "warning_count": len(warnings),
    }


def _history_row(row, errors, warnings):
    if not isinstance(row, dict):
        return {}
    sheet = row.get("sheet")
    row_number = row.get("row")
    issues = [
        issue
        for issue in [*_issue_list(errors), *_issue_list(warnings)]
        if issue.get("sheet") == sheet and issue.get("row") == row_number
    ]
    skip_reason = str(row.get("skip_reason", "") or "").strip()
    reasons = [skip_reason] if skip_reason else []
    reasons.extend(
        str(issue.get("message", ""))
        for issue in issues
        if issue.get("message") and issue.get("message") not in reasons
    )
    return {
        "row_key": row.get("row_key"),
        "record_type": row.get("record_type"),
        "sheet": sheet,
        "row": row_number,
        "action": row.get("action"),
        "order_no": row.get("order_no", ""),
        "item_no": row.get("item_no", ""),
        "specification": row.get("specification", ""),
        "material": row.get("material", ""),
        "summary": row.get("summary", "") or " / ".join(
            str(value)
            for value in (
                row.get("order_no"),
                row.get("item_no"),
                row.get("specification"),
                row.get("batch_no"),
            )
            if value not in (None, "")
        ),
        "changes": row.get("changes", {}),
        "skip_reason_code": row.get("skip_reason_code", ""),
        "skip_reason": skip_reason,
        "reasons": reasons,
        "issues": issues,
        "valid": not any(issue.get("level") == "error" for issue in issues),
    }


class BusinessImportHistoryView(APIView):
    @extend_schema(operation_id="orders_import_history_list", responses=dict)
    def get(self, request):
        queryset = BusinessImportBatch.objects.filter(created_by=request.user)
        q = str(request.query_params.get("q", "") or "").strip()
        status = str(request.query_params.get("status", "") or "").strip().upper()
        source_type = (
            str(request.query_params.get("source_type", "") or "").strip().upper()
        )
        if q:
            queryset = queryset.filter(original_name__icontains=q)
        if status:
            if status not in BusinessImportBatch.Status.values:
                raise DRFValidationError({"status": "无效的导入状态。"})
            queryset = queryset.filter(status=status)
        if source_type:
            if source_type not in BusinessImportBatch.SourceType.values:
                raise DRFValidationError({"source_type": "无效的导入来源类型。"})
            queryset = queryset.filter(source_type=source_type)
        queryset = queryset.order_by("-created_at", "-id")
        paginator = BusinessPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response([_batch_summary(batch) for batch in page])


class BusinessImportHistoryDetailView(APIView):
    @extend_schema(operation_id="orders_import_history_retrieve", responses=dict)
    def get(self, request, token):
        batch = get_object_or_404(
            BusinessImportBatch, pk=token, created_by=request.user
        )
        payload = _batch_summary(batch)
        payload.update(
            {
                "issues": [*_issue_list(batch.errors), *_issue_list(batch.warnings)],
                "rows": [
                    _history_row(row, batch.errors, batch.warnings)
                    for row in _batch_rows(batch)
                ],
            }
        )
        return Response(payload)
