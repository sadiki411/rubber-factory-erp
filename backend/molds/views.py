import io
import re
from zipfile import BadZipFile
from uuid import UUID

from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, connection
from django.db.models import Count, F, Prefetch, Q
from django.db.models.deletion import ProtectedError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from drf_spectacular.utils import extend_schema
from openpyxl import Workbook
from openpyxl.utils.exceptions import InvalidFileException
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView, exception_handler as drf_exception_handler

from .imports import commit_batch, create_standard_template, preview_workbook
from .models import (
    ImportBatch,
    Machine,
    MoldAsset,
    MoldModel,
    Processor,
    Rack,
    RackLevel,
    RackSlot,
    RackZone,
)
from .serializers import (
    CapacitySerializer,
    MachineSerializer,
    MoldActionSerializer,
    MoldAssetSerializer,
    MoldDeleteSerializer,
    MoldModelSerializer,
    MoldMovementSerializer,
    ProcessorSerializer,
    RackConfigSerializer,
    RackSummarySerializer,
    SlotSerializer,
    StackingSerializer,
)
from .services import (
    ConfirmationRequired,
    archive_mold,
    build_rack_preview,
    configure_rack,
    switch_zone_capacity,
    switch_zone_stacking,
    transition_mold,
)


def _django_validation_payload(exc):
    if hasattr(exc, "message_dict"):
        return exc.message_dict
    messages = list(getattr(exc, "messages", []))
    if len(messages) == 1:
        return {"detail": messages[0]}
    return {"detail": messages or [str(exc)]}


def api_exception_handler(exc, context):
    """Return stable JSON for domain validation and confirmable safety warnings."""

    if isinstance(exc, ConfirmationRequired):
        return Response(
            {
                "detail": "该操作存在叠放风险，需要二次确认。",
                "requires_confirmation": True,
                "warnings": list(exc.warnings),
            },
            status=status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, DjangoValidationError):
        return Response(_django_validation_payload(exc), status=status.HTTP_400_BAD_REQUEST)
    if isinstance(exc, ProtectedError):
        return Response(
            {"detail": "该资料已被模具或历史记录使用，不能删除；可改为停用。"},
            status=status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, IntegrityError):
        return Response(
            {"detail": "数据发生唯一性或关联冲突，请刷新后重试。"},
            status=status.HTTP_409_CONFLICT,
        )
    return drf_exception_handler(exc, context)


def _user_payload(user):
    return {
        "id": user.pk,
        "username": user.get_username(),
        "display_name": user.get_full_name() or user.get_username(),
    }


@method_decorator(ensure_csrf_cookie, name="dispatch")
class SessionView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [AllowAny]

    @extend_schema(responses=dict)
    def get(self, request):
        if not request.user.is_authenticated:
            return Response({"authenticated": False})
        return Response({"authenticated": True, "user": _user_payload(request.user)})


@method_decorator(csrf_protect, name="dispatch")
class LoginView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    @extend_schema(request=dict, responses=dict)
    def post(self, request):
        username = str(request.data.get("username", "")).strip()
        password = str(request.data.get("password", ""))
        errors = {}
        if not username:
            errors["username"] = ["请输入用户名。"]
        if not password:
            errors["password"] = ["请输入密码。"]
        if errors:
            raise DRFValidationError(errors)

        user = authenticate(request=request._request, username=username, password=password)
        if user is None:
            return Response({"detail": "用户名或密码错误。"}, status=status.HTTP_400_BAD_REQUEST)
        if not user.is_active:
            return Response({"detail": "该账号已停用。"}, status=status.HTTP_403_FORBIDDEN)

        auth_login(request._request, user)
        return Response({"authenticated": True, "user": _user_payload(user)})


@method_decorator(csrf_protect, name="dispatch")
class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses={204: None})
    def post(self, request):
        auth_logout(request._request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class HealthView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(responses=dict)
    def get(self, request):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        except Exception:
            return Response(
                {"status": "error", "database": "unavailable"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response({"status": "ok", "database": "ok"})


class FlexiblePageNumberPagination(PageNumberPagination):
    page_size = 30
    page_size_query_param = "page_size"
    max_page_size = 1000


class MoldViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = MoldAssetSerializer
    pagination_class = FlexiblePageNumberPagination

    def get_queryset(self):
        queryset = MoldAsset.objects.select_related(
            "mold_model",
            "current_slot__zone__level__rack",
            "current_machine",
            "current_processor",
        )
        include_inactive = (
            self.request.method in {"GET", "HEAD", "OPTIONS"}
            and str(self.request.query_params.get("include_inactive", "")).lower()
            in {"1", "true", "yes"}
        )
        if not include_inactive:
            queryset = queryset.filter(is_active=True)
        keyword = str(self.request.query_params.get("q", "")).strip()
        if keyword:
            queryset = queryset.filter(
                Q(asset_code__icontains=keyword)
                | Q(mold_model__code__icontains=keyword)
                | Q(mold_model__product_name__icontains=keyword)
            )
        mold_status = str(self.request.query_params.get("status", "")).strip()
        if mold_status:
            if mold_status not in MoldAsset.Status.values:
                raise DRFValidationError({"status": "无效的模具状态。"})
            queryset = queryset.filter(status=mold_status)
        return queryset.order_by("asset_code")

    @extend_schema(request=MoldDeleteSerializer, responses={204: None})
    def destroy(self, request, *args, **kwargs):
        serializer = MoldDeleteSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        archive_mold(
            self.get_object(),
            request.user,
            note=serializer.validated_data.get("note", ""),
            confirm_warnings=serializer.validated_data["confirm_warnings"],
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(responses=MoldMovementSerializer(many=True))
    @action(detail=True, methods=["get"], pagination_class=None)
    def history(self, request, pk=None):
        mold = self.get_object()
        movements = mold.movements.select_related(
            "from_slot__zone__level__rack",
            "to_slot__zone__level__rack",
            "from_machine",
            "to_machine",
            "from_processor",
            "to_processor",
            "operator",
        )
        return Response(MoldMovementSerializer(movements, many=True).data)

    def _perform_domain_action(self, request, movement_action):
        serializer = MoldActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        mold, _warnings = transition_mold(
            self.get_object(),
            movement_action,
            request.user,
            **serializer.validated_data,
        )
        return Response(MoldAssetSerializer(mold, context={"request": request}).data)

    @extend_schema(request=MoldActionSerializer, responses=MoldAssetSerializer)
    @action(detail=True, methods=["post"], url_path="actions/putaway")
    def putaway(self, request, pk=None):
        return self._perform_domain_action(request, "PUTAWAY")

    @extend_schema(request=MoldActionSerializer, responses=MoldAssetSerializer)
    @action(detail=True, methods=["post"], url_path="actions/move")
    def move(self, request, pk=None):
        return self._perform_domain_action(request, "MOVE")

    @extend_schema(request=MoldActionSerializer, responses=MoldAssetSerializer)
    @action(detail=True, methods=["post"], url_path="actions/load-machine")
    def load_machine(self, request, pk=None):
        return self._perform_domain_action(request, "LOAD_MACHINE")

    @extend_schema(request=MoldActionSerializer, responses=MoldAssetSerializer)
    @action(detail=True, methods=["post"], url_path="actions/send-out")
    def send_out(self, request, pk=None):
        return self._perform_domain_action(request, "SEND_OUT")


class MasterDataViewSet(viewsets.ModelViewSet):
    pagination_class = None


class MoldModelViewSet(MasterDataViewSet):
    serializer_class = MoldModelSerializer

    def get_queryset(self):
        return MoldModel.objects.annotate(asset_count=Count("assets")).order_by("code")


class MachineViewSet(MasterDataViewSet):
    serializer_class = MachineSerializer

    def get_queryset(self):
        return Machine.objects.annotate(current_mold_count=Count("current_molds")).order_by("code")


class ProcessorViewSet(MasterDataViewSet):
    serializer_class = ProcessorSerializer

    def get_queryset(self):
        return Processor.objects.annotate(current_mold_count=Count("current_molds")).order_by("code")


class RackApiSerializer(RackSummarySerializer):
    occupied_count = serializers.IntegerField(read_only=True, required=False)
    active_slot_count = serializers.IntegerField(read_only=True, required=False)

    class Meta(RackSummarySerializer.Meta):
        fields = [*RackSummarySerializer.Meta.fields, "occupied_count", "active_slot_count"]

    def validate_code(self, value):
        value = value.strip().upper()
        if not re.fullmatch(r"[A-Z0-9_-]+", value):
            raise serializers.ValidationError("只能使用字母、数字、横线和下划线。")
        queryset = Rack.objects.filter(code__iexact=value)
        if self.instance:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise serializers.ValidationError("该货架编号已存在。")
        return value


def _rack_queryset():
    return Rack.objects.annotate(
        level_count=Count("levels", distinct=True),
        occupied_count=Count("levels__zones__slots__occupant", distinct=True),
        active_slot_count=Count(
            "levels__zones__slots",
            filter=(Q(
                levels__zones__is_active=True,
                levels__zones__slots__is_blocked=False,
                levels__zones__slots__capacity_mode=F("levels__zones__capacity_mode"),
            ) & (
                Q(levels__zones__slots__stack_level=1)
                | Q(levels__zones__stacking_enabled=True)
            )),
            distinct=True,
        ),
    ).order_by("code")


def _load_rack_layout(rack_id):
    current_slots = RackSlot.objects.filter(
        Q(capacity_mode=F("zone__capacity_mode"))
        & (Q(stack_level=1) | Q(zone__stacking_enabled=True))
    ).select_related("occupant__mold_model").order_by("position_no", "stack_level")
    zones = RackZone.objects.order_by("code").prefetch_related(
        Prefetch("slots", queryset=current_slots, to_attr="layout_slots")
    )
    levels = RackLevel.objects.order_by("-level_no").prefetch_related(
        Prefetch("zones", queryset=zones)
    )
    return get_object_or_404(
        Rack.objects.prefetch_related(Prefetch("levels", queryset=levels)),
        pk=rack_id,
    )


def _rack_layout_payload(rack):
    level_payloads = []
    occupied_count = 0
    active_slot_count = 0
    for level in rack.levels.all():
        zone_payloads = []
        for zone in level.zones.all():
            slots = list(getattr(zone, "layout_slots", []))
            slot_payloads = SlotSerializer(slots, many=True).data
            occupied_count += sum(1 for slot in slots if hasattr(slot, "occupant"))
            active_slot_count += sum(1 for slot in slots if slot.is_enabled)
            zone_payloads.append(
                {
                    "id": zone.id,
                    "code": zone.code,
                    "label": zone.label,
                    "name": zone.label,
                    "allowed_capacities": zone.allowed_capacities,
                    "capacity_mode": zone.capacity_mode,
                    "current_capacity": zone.capacity_mode,
                    "supports_stacking": zone.supports_stacking,
                    "stacking_enabled": zone.stacking_enabled,
                    "stack_levels": 2 if zone.stacking_enabled else 1,
                    "is_active": zone.is_active,
                    "slots": slot_payloads,
                }
            )
        level_payloads.append(
            {"id": level.id, "level_no": level.level_no, "zones": zone_payloads}
        )
    rack_payload = dict(RackSummarySerializer(rack).data)
    rack_payload.update(
        {
            "configured": rack.is_configured,
            "locked": rack.structure_locked,
            "level_count": len(level_payloads),
            "occupied_count": occupied_count,
            "active_slot_count": active_slot_count,
        }
    )
    return {"rack": rack_payload, "levels": level_payloads}


def _preview_layout_payload(preview, name=""):
    levels = []
    for level in preview["levels"]:
        zones = []
        for zone in level["zones"]:
            zone_active = zone.get("is_active", True)
            stacking_enabled = zone.get(
                "stacking_enabled", zone.get("default_stacking_enabled", False)
            )
            slots = []
            for slot in zone["slots"]:
                if slot.get("stack_level", 1) == 2 and not stacking_enabled:
                    continue
                slot_blocked = bool(slot.get("is_blocked", not zone_active))
                slots.append(
                    {
                        **slot,
                        "capacity_mode": zone["default_capacity"],
                        "is_enabled": zone_active and not slot_blocked,
                        "is_blocked": slot_blocked,
                        "blocking_reason": slot.get("blocking_reason", zone.get("blocking_reason", "")),
                        "occupied": False,
                        "occupant": None,
                    }
                )
            zones.append(
                {
                    "code": zone["code"],
                    "label": zone["label"],
                    "name": zone["label"],
                    "allowed_capacities": zone["allowed_capacities"],
                    "capacity_mode": zone["default_capacity"],
                    "current_capacity": zone["default_capacity"],
                    "supports_stacking": zone["supports_stacking"],
                    "stacking_enabled": stacking_enabled,
                    "stack_levels": 2 if stacking_enabled else 1,
                    "is_active": zone_active,
                    "slots": slots,
                }
            )
        levels.append({"level_no": level["level_no"], "zones": zones})
    return {
        "rack": {
            "id": None,
            "code": preview["rack_code"],
            "name": name or f"{preview['rack_code']} 预览",
            "is_configured": False,
            "structure_locked": False,
            "configured": False,
            "locked": False,
            "level_count": preview["level_count"],
        },
        "levels": levels,
    }


class RackViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = RackApiSerializer
    pagination_class = None

    def get_queryset(self):
        return _rack_queryset()

    @extend_schema(request=RackConfigSerializer, responses=dict)
    @action(detail=False, methods=["post"], url_path="config-preview")
    def config_preview(self, request):
        rack_code = str(request.data.get("rack_code", "")).strip().upper()
        if not rack_code or len(rack_code) > 20 or not re.fullmatch(r"[A-Z0-9_-]+", rack_code):
            raise DRFValidationError({"rack_code": "请输入有效的货架编号。"})
        serializer = RackConfigSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        preview = build_rack_preview(rack_code, serializer.validated_data)
        return Response(_preview_layout_payload(preview, str(request.data.get("name", "")).strip()))

    @extend_schema(request=RackConfigSerializer, responses=dict)
    @action(detail=True, methods=["post"])
    def configure(self, request, pk=None):
        serializer = RackConfigSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        rack = configure_rack(self.get_object(), serializer.validated_data)
        return Response(_rack_layout_payload(_load_rack_layout(rack.pk)))

    @extend_schema(request=CapacitySerializer, responses=dict)
    @action(
        detail=True,
        methods=["post"],
        url_path=r"zones/(?P<zone_id>\d+)/capacity",
    )
    def capacity(self, request, pk=None, zone_id=None):
        rack = self.get_object()
        zone = get_object_or_404(
            RackZone.objects.select_related("level__rack"),
            pk=zone_id,
            level__rack_id=rack.pk,
        )
        serializer = CapacitySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        switch_zone_capacity(zone, serializer.validated_data["capacity"])
        return Response(_rack_layout_payload(_load_rack_layout(rack.pk)))

    @extend_schema(request=StackingSerializer, responses=dict)
    @action(
        detail=True,
        methods=["post"],
        url_path=r"zones/(?P<zone_id>\d+)/stacking",
    )
    def stacking(self, request, pk=None, zone_id=None):
        rack = self.get_object()
        zone = get_object_or_404(
            RackZone.objects.select_related("level__rack"),
            pk=zone_id,
            level__rack_id=rack.pk,
        )
        serializer = StackingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        switch_zone_stacking(zone, serializer.validated_data["enabled"])
        return Response(_rack_layout_payload(_load_rack_layout(rack.pk)))

    @extend_schema(responses=dict)
    @action(detail=True, methods=["get"])
    def layout(self, request, pk=None):
        return Response(_rack_layout_payload(_load_rack_layout(pk)))


class SlotViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = SlotSerializer
    pagination_class = None

    def get_queryset(self):
        queryset = RackSlot.objects.select_related(
            "zone__level__rack", "occupant__mold_model"
        ).filter(
            Q(capacity_mode=F("zone__capacity_mode"))
            & (Q(stack_level=1) | Q(zone__stacking_enabled=True))
        )
        rack_id = self.request.query_params.get("rack_id")
        if rack_id:
            queryset = queryset.filter(zone__level__rack_id=rack_id)
        available = str(self.request.query_params.get("available", "")).lower()
        if available in {"1", "true", "yes"}:
            queryset = queryset.filter(
                zone__is_active=True,
                zone__level__rack__is_active=True,
                zone__level__rack__is_configured=True,
                is_blocked=False,
                occupant__isnull=True,
            )
        return queryset.order_by(
            "zone__level__rack__code",
            "-zone__level__level_no",
            "zone__code",
            "position_no",
            "stack_level",
        )


class ImportTemplateView(APIView):
    @extend_schema(responses={(200, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"): bytes})
    def get(self, request):
        response = HttpResponse(
            create_standard_template(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = "attachment; filename*=UTF-8''mold-erp-import-template.xlsx"
        return response


class ImportPreviewView(APIView):
    @extend_schema(request={"multipart/form-data": dict}, responses=dict)
    def post(self, request):
        uploaded_file = request.FILES.get("file")
        if uploaded_file is None:
            raise DRFValidationError({"file": "请选择Excel文件。"})
        if not uploaded_file.name.lower().endswith(".xlsx"):
            raise DRFValidationError({"file": "仅支持.xlsx文件。"})
        try:
            result = preview_workbook(uploaded_file, request.user)
        except (ValueError, KeyError, OSError, BadZipFile, InvalidFileException) as exc:
            raise DRFValidationError({"file": str(exc)}) from exc
        return Response(result)


class ImportCommitView(APIView):
    @extend_schema(request=dict, responses=dict)
    def post(self, request):
        token = str(request.data.get("token", "")).strip()
        try:
            batch_id = UUID(token)
        except (ValueError, TypeError, AttributeError) as exc:
            raise DRFValidationError({"token": "无效的导入批次标识。"}) from exc
        batch = get_object_or_404(ImportBatch, pk=batch_id, created_by=request.user)
        row_updates = request.data.get("rows") or []
        if not isinstance(row_updates, list):
            raise DRFValidationError({"rows": "编号修改内容必须是列表。"})
        try:
            imported = commit_batch(batch, request.user, asset_code_updates=row_updates)
        except ValueError as exc:
            raise DRFValidationError({"detail": str(exc)}) from exc
        return Response({"imported_count": imported})


class ImportErrorReportView(APIView):
    @extend_schema(responses={(200, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"): bytes})
    def get(self, request, token):
        batch = get_object_or_404(ImportBatch, pk=token, created_by=request.user)
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "预检问题"
        sheet.append(["级别", "工作表", "行号", "字段", "说明"])
        for issue in [*batch.errors, *batch.warnings]:
            sheet.append(
                [
                    issue.get("level", ""),
                    issue.get("sheet", ""),
                    issue.get("row", ""),
                    issue.get("field", ""),
                    issue.get("message", ""),
                ]
            )
        for cell in sheet[1]:
            cell.font = cell.font.copy(bold=True)
        sheet.freeze_panes = "A2"
        sheet.column_dimensions["A"].width = 12
        sheet.column_dimensions["B"].width = 24
        sheet.column_dimensions["C"].width = 10
        sheet.column_dimensions["D"].width = 20
        sheet.column_dimensions["E"].width = 60
        output = io.BytesIO()
        workbook.save(output)
        response = HttpResponse(
            output.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = (
            f"attachment; filename=import-errors-{batch.pk}.xlsx"
        )
        return response
