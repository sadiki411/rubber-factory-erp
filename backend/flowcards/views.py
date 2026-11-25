import hashlib
import io
import uuid
import re
from types import SimpleNamespace
from collections.abc import Mapping
from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import Exists, Max, OuterRef, Q, Subquery
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from PIL import Image, UnidentifiedImageError
from rest_framework import viewsets
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError, APIException
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated

from orders.models import ProductInspectionCriterion, ProductSpecification
from production.models import ProductionRun
from quality.models import QualityOrder, QualityReworkCase, QualityShipmentBatch
from . import services as s
from .limits import limit_text, limit_mode
from .paper import A4_TWO_UP, SINGLE
from .measurements import applicable_criteria, reviewed_measurements
from .models import (AppearanceIssue, StandardRevision, CardGroup, InternalCard, CardEvent,
                     PrintJob, PrintItem, Attachment, PositionDiagram, TemplatePublication, ShipmentSource)


class FlowEnvelopeSerializer(serializers.Serializer):
    """Endpoint payloads are further validated by flowcards.services; no ORM writes here."""
    id = serializers.CharField(read_only=True)
    request_key = serializers.CharField(required=False, max_length=128)
    status = serializers.CharField(read_only=True)
    snapshot = serializers.JSONField(read_only=True)


class ErrorsMixin:
    serializer_class = FlowEnvelopeSerializer

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if request.method not in ("GET", "HEAD", "OPTIONS") and not isinstance(request.data, Mapping):
            raise ValidationError({"detail": "请求内容必须是对象，不接收无明确字段的数组/文本。"})
    def handle_exception(self, exc):
        if isinstance(exc, IntegrityError):
            return Response({"detail": "并发请求发生冲突，未重复登记。请用原请求号查询/重试。"}, status=409)
        if isinstance(exc, OperationalError) and "locked" in str(exc).lower():
            return Response({"detail": "记录正在更新，结果请按原请求号核对，不要另建请求。"}, status=503)
        return super().handle_exception(exc)


class Pagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


def page(view, request, qs, mapper):
    pagination = Pagination()
    rows = pagination.paginate_queryset(qs, request, view=view)
    return pagination.get_paginated_response([mapper(row) for row in rows])


def card_data(card):
    from .preparation import package_progress
    pending = getattr(card, "pending_print_job_id", None)
    return {"id": str(card.pk), "card_no": card.card_no, "code": f"ERPFC:{card.pk}",
            "group_id": str(card.group_id), "sequence": card.sequence, "denominator": card.denominator,
            "planned_molds": card.planned_molds, "original_date": card.original_date,
            "status": card.status, "printed_at": card.printed_at, "reprint_count": card.reprint_count,
            "pending_print_job_id": str(uuid.UUID(str(pending))) if pending else None,
            "has_usage": getattr(card, "has_usage", None), "process_todos": package_progress(card)}


def with_card_actions(cards):
    return cards.annotate(
        pending_print_job_id=Subquery(PrintItem.objects.filter(card_id=OuterRef("pk"), active=True).values("job_id")[:1]),
        has_usage=(Exists(CardEvent.objects.filter(cards=OuterRef("pk")).exclude(kind="VOID"))
                   | Exists(ShipmentSource.objects.filter(card_id=OuterRef("pk")))
                   | Exists(Attachment.objects.filter(card_id=OuterRef("pk")))),
    )


def group_data(group):
    return {"id": str(group.pk), "product_id": group.product_id, "run_id": group.run_id,
            "planned_molds": group.planned_molds, "molds_per_card": group.molds_per_card,
            "plan_basis": group.plan_basis, "whole_mold": group.whole_mold, "status": group.status,
            "snapshot": group.snapshot, "generated_at": group.generated_at,
            "created_at": group.created_at, "request_key": group.request_key,
            "revision": group.revision, "range_start": group.range_start, "range_end": group.range_end,
            "updated_at": group.updated_at, "updated_by": group.updated_by_id}


def event_data(event):
    return {"id": str(event.pk), "kind": event.kind, "business_at": event.business_at,
            "created_at": event.created_at, "recorded_by": event.created_by_id,
            "employee_id": event.employee_id, "employee_name": event.employee_name,
            "card_ids": [str(c.pk) for c in event.cards.all()],
            "card_refs": [{"id": str(c.pk), "card_no": c.card_no} for c in event.cards.all()],
            "followups": [{"id": str(e.pk), "employee_name": e.employee_name, "business_at": e.business_at,
                "message": e.values.get("measurement_message", "未判定"), "judgment": e.values.get("judgment", "NOT_EVALUATED"),
                "review_mode": e.values.get("review_mode", "RETEST"), "correction_reason": e.values.get("correction_reason", "")}
                for e in event.cardevent_set.all()] if event.kind in s.MEASURE_STAGES else [],
            "values": event.values, "scope": event.scope, "evidence": event.evidence,
            "replaces_id": str(event.replaces_id) if event.replaces_id else None}


def source_returns(source):
    tracking = [ref["tracking_id"] for ref in source.snapshot.get("involved_customer_cards", [])]
    if source.customer_tracking_id:
        tracking.append(source.customer_tracking_id)
    return QualityReworkCase.objects.filter(Q(shipment_batch_id=source.batch_id) | Q(process_card__tracking_id__in=tracking)).order_by("id")


def return_data(row):
    return {"id": row.pk, "case_no": row.case_no, "status": row.status, "return_round": row.return_round,
            "reshipment_batch_id": row.reshipment_batch_id}


class ContextView(ErrorsMixin, APIView):
    def get(self, request):
        from . import preparation as prep
        if prep.enabled():
            result = prep.context(request.query_params, request.user)
            result["appearance"] = list(AppearanceIssue.objects.filter(is_active=True).values("id", "name"))
            result["diagrams"] = list(PositionDiagram.objects.filter(product_id=result["product"]["id"], mold_id=result["mold_id"]).values("id", "mold_id", "version", "direction", "scope", "positions")) if result["mold_id"] else []
            return Response(result)
        run = s.get_row(ProductionRun, request.query_params["run_id"], "run_id") if request.query_params.get("run_id") else None
        order = s.get_row(QualityOrder, request.query_params["order_id"], "order_id") if request.query_params.get("order_id") else None
        pid = request.query_params.get("product_id") or (run.product_specification_id if run else None) or (order.product_specification_id if order else None)
        product = s.get_row(ProductSpecification, pid, "product_id")
        if run and run.product_specification_id != product.pk or order and order.product_specification_id != product.pk:
            raise ValidationError({"product_id": "来源产品不一致。"})
        ids = list(run.order_links.values_list("order_id", flat=True)) if run else []
        if run and run.order_id and run.order_id not in ids:
            ids.append(run.order_id)
        if order and not run:
            ids = [order.pk]
        orders = list(QualityOrder.objects.filter(pk__in=ids).select_related("source_batch", "last_source_batch"))
        standards = StandardRevision.objects.filter(product=product)
        if run:
            standards = standards.filter(Q(mold_id=run.mold_id) | Q(mold__isnull=True))
        existing = CardGroup.objects.filter(run=run) if run else CardGroup.objects.filter(orders=order) if order else CardGroup.objects.filter(product=product)
        criteria = ProductInspectionCriterion.objects.filter(product_specification=product).order_by("id")[:100]
        if ids:
            criteria = ProductInspectionCriterion.objects.filter(product_specification=product).filter(Q(order__isnull=True) | Q(order_id__in=ids)).order_by("id")[:100]
        return Response({"write_enabled": settings.FLOW_CARDS_WRITE_ENABLED,
            "product": {"id": product.pk, "name": product.product_name, "specification": product.specification, "material": product.material},
            "raw_process_reference": {"secondary_curing": product.secondary_curing, "primary_curing": product.primary_curing,
                                      "total_cavities": product.total_cavities, "effective_cavities": product.effective_cavities},
            "run_id": run.pk if run else None, "mold_id": run.mold_id if run else None,
            "order_ids": ids, "orders": [{"id": o.pk, "order_no": o.order_no, "item_no": o.item_no, "customer": s.order_customer_key(o)} for o in orders],
            "suggested_molds": run.planned_mold_count if run else None,
            "standards": [s.standard_data(row) for row in standards.order_by("-mold_id", "-version")[:20]],
            "existing_groups": [group_data(g) for g in existing.distinct().order_by("-created_at")[:20]],
            "appearance": list(AppearanceIssue.objects.filter(is_active=True).values("id", "name")),
            "criteria": [{"id": c.pk, "order_id": c.order_id, "source_sheet": c.source_sheet, "source_row": c.source_row,
                "inspection_item": c.inspection_item, "lower_limit": c.lower_limit,
                "upper_limit": c.upper_limit, "unit": c.unit, "method": (c.raw_data or {}).get("method", ""),
                "limit_mode": (c.raw_data or {}).get("limit_mode"),
                "upper_inclusive": (c.raw_data or {}).get("upper_inclusive", True),
                "lower_inclusive": (c.raw_data or {}).get("lower_inclusive", True)} for c in criteria],
            "requirement_warnings": s.requirement_warnings(product, orders),
            "diagrams": list(PositionDiagram.objects.filter(product=product, mold_id=run.mold_id).values("id", "mold_id", "version", "direction", "scope", "positions")) if run and run.mold_id else [],
            "limitations": ["显示计划关联，不代表已生产或实物来源已核对。", "已制卡计划不进入生产/库存/交付数量。"]})


class StandardView(ErrorsMixin, APIView):
    def get(self, request):
        qs = StandardRevision.objects.all().order_by("-version")
        if request.query_params.get("product_id"):
            qs = qs.filter(product_id=s.integer(request.query_params["product_id"], "product_id"))
        return page(self, request, qs, s.standard_data)

    def post(self, request):
        return Response(s.standard_data(s.confirm_standard(request.data, request.user)), status=201)


class StandardActivationView(ErrorsMixin, APIView):
    def post(self, request, pk):
        from .preparation import activate_standard
        return Response(s.standard_data(activate_standard(s.get_row(StandardRevision, pk, "standard_id"), request.user)))


class StandardImportPreviewView(ErrorsMixin, APIView):
    """No persistence: the user selects the existing product before saving a revision."""
    def post(self, request):
        from . import preparation as prep
        from .standard_imports import preview_workbook
        prep.require_maintainer(request.user)
        upload = request.FILES.get("file")
        if not upload or not upload.name.lower().endswith(".xlsx") or upload.size > 8 * 1024 * 1024:
            raise ValidationError({"file": "请上传8MB以内的xlsx标准表；图片仅作人工参考。"})
        result = preview_workbook(upload.read())
        result["filename"] = Path(upload.name).name
        return Response(result)


class PreparationSourcesView(ErrorsMixin, APIView):
    def get(self, request):
        from . import preparation as prep
        product, run, orders, _ = prep.resolve(request.query_params)
        return page(self, request, prep.source_rows(product, orders), prep.criterion_data)


class PreparationSourceFileView(ErrorsMixin, APIView):
    def get(self, request, pk):
        from orders.models import BusinessImportBatch
        batch = get_object_or_404(BusinessImportBatch, pk=pk)
        try:
            response = FileResponse(batch.original_file.open("rb"), as_attachment=True, filename=Path(batch.original_name).name)
        except (FileNotFoundError, ValueError, OSError):
            return Response({"detail": "原件未保存或文件缺失，不能用识别文本冒充原件。"}, status=503)
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Security-Policy"] = "sandbox"
        return response


class AppearanceView(ErrorsMixin, APIView):
    def post(self, request):
        s.write_enabled()
        from . import preparation as prep
        if prep.enabled():
            prep.require_maintainer(request.user)
        name = s.required_text(request.data, "name", 40)
        active = request.data.get("is_active", True)
        if not isinstance(active, bool):
            raise ValidationError({"is_active": "状态必须是布尔值。"})
        with transaction.atomic():
            row = s.get_row(AppearanceIssue, request.data["id"], "id") if request.data.get("id") else AppearanceIssue(code="USER_" + uuid.uuid4().hex)
            if AppearanceIssue.objects.filter(name__iexact=name).exclude(pk=row.pk).exists():
                raise ValidationError({"name": "问题名称已存在，请使用原条目，不重复建立。"})
            before = {"name": row.name, "is_active": row.is_active} if row.pk else None
            row.name, row.is_active = name, active
            row.save()
            s.audit(row, "APPEARANCE_LIBRARY", request.user, {"before": before, "after": {"name": name, "is_active": active}})
        return Response({"id": row.pk, "name": row.name, "is_active": row.is_active})


class GroupViewSet(ErrorsMixin, viewsets.GenericViewSet):
    queryset = CardGroup.objects.all()

    def get_permissions(self):
        # Explicit exception for a computational, non-persistent POST only.
        if self.action in ("preview", "sample") and settings.FLOW_CARDS_PREPARATION_ENABLED:
            return [IsAuthenticated()]
        return super().get_permissions()

    def list(self, request):
        qs = self.get_queryset().order_by("-created_at")
        for key in ("product_id", "run_id", "request_key"):
            if request.query_params.get(key):
                value = request.query_params[key] if key == "request_key" else s.integer(request.query_params[key], key, maximum=2147483647)
                qs = qs.filter(**{key: value})
        if request.query_params.get("order_id"):
            qs = qs.filter(orders__id=s.integer(request.query_params["order_id"], "order_id", maximum=2147483647))
        if request.query_params.get("q"):
            term = request.query_params["q"].strip()[:150]
            qs = qs.filter(Q(cards__card_no__icontains=term) | Q(orders__order_no__icontains=term) | Q(plan_basis__icontains=term) | Q(request_key=term))
        return page(self, request, qs.distinct(), group_data)

    def retrieve(self, request, pk=None):
        return Response(group_data(self.get_object()))

    def create(self, request):
        return Response(group_data(s.create_group(request.data, request.user)), status=201)

    @action(detail=True, methods=["post"])
    def revise(self, request, pk=None):
        return Response(group_data(s.revise_draft(self.get_object(), request.data, request.user)))

    @action(detail=False, methods=["post"])
    def preview(self, request):
        # Preview computes a plan but creates NO identity or quantity records.
        from . import preparation as prep
        plan = prep.build_plan(request.data, request.data.get("group_id")) if prep.enabled() else s.group_plan(request.data)
        return Response({"count": plan["count"], "planned_molds": plan["planned"],
            "tail_molds": plan["snapshot"]["card_molds"][-1] if plan["count"] else None,
            "card_molds": plan["snapshot"]["card_molds"],
            "snapshot": plan["snapshot"]})

    @action(detail=False, methods=["post"])
    def sample(self, request):
        """Same renderer as issuance, but no identity, QR or database writes."""
        from . import preparation as prep
        from .pdf import render_cards
        if not prep.enabled():
            raise Http404("订单纸卡样张未启用。")
        plan = prep.build_plan(request.data, request.data.get("group_id"))
        if not plan["count"]:
            raise ValidationError({"planned_molds": "请先填写预计模数，或核对本次安排件数与有效产出后试算。"})
        which = request.data.get("sample_position", "FIRST")
        if which not in ("FIRST", "LAST"):
            raise ValidationError({"sample_position": "请选择首卡或尾卡样张。"})
        options = s.print_options(request.data)
        count = plan["count"]
        indices = [0 if which == "FIRST" else count - 1]
        if options["paper_layout"] == A4_TWO_UP:
            indices = range(min(count, 2)) if which == "FIRST" else range((count - 1)//2*2, count)
        publication = TemplatePublication.objects.filter(renderer=s.current_renderer()).first()
        snapshot = {**plan["snapshot"], "sample_only": True, "template": {**plan["snapshot"]["template"], "version": publication.version if publication else "草稿"}}
        cards = [SimpleNamespace(pk=None, card_no="样张 - 未发号", original_date=timezone.localdate(),
            sequence=index+1, denominator=count, planned_molds=snapshot["card_molds"][index], group=SimpleNamespace(snapshot=snapshot)) for index in indices]
        raw = render_cards([(card, 0) for card in cards], print_options=options)
        response = HttpResponse(raw, content_type="application/pdf")
        response["Content-Disposition"] = 'inline; filename="flowcard-sample-not-issued.pdf"'
        response["Cache-Control"] = "private, no-store"
        return response

    @action(detail=True, methods=["post"])
    def generate(self, request, pk=None):
        group = self.get_object()
        if settings.FLOW_CARDS_PREPARATION_ENABLED and group.status == "DRAFT":
            from .preparation import PreparationConflict
            if request.data.get("expected_revision") != group.revision:
                raise PreparationConflict("准备资料版本已变化，请重新读取核对后再发卡。")
        return Response(group_data(s.generate_group(group, request.user, expected_revision=request.data.get("expected_revision"))))

    @action(detail=True, methods=["get"])
    def cards(self, request, pk=None):
        cards = self.get_object().cards.select_related("group").prefetch_related("events").order_by("sequence")
        return page(self, request, with_card_actions(cards), card_data)


class CardViewSet(ErrorsMixin, viewsets.GenericViewSet):
    queryset = with_card_actions(InternalCard.objects.select_related("group"))

    @action(detail=False, methods=["get"])
    def scan(self, request):
        code = request.query_params.get("code", "").strip().upper()
        # Only remove a printed line break at an existing separator. Never
        # infer a missing character or perform tail-number fuzzy matching.
        if re.fullmatch(r"FC-\d{8}-\s*\n\s*[A-Z0-9]+", code):
            code = re.sub(r"\s+", "", code)
        try:
            if code.startswith("ERPFC:"):
                card = self.get_queryset().get(pk=uuid.UUID(code[6:]))
            elif code.startswith("FC-"):
                card = self.get_queryset().get(card_no=code)
            else:
                return Response({"found": False, "type": "NOT_INTERNAL", "detail": "不是内部工序卡。"}, status=400)
        except (ValueError, InternalCard.DoesNotExist):
            return Response({"found": False, "type": "INTERNAL_UNKNOWN", "detail": "内部卡不存在，不建立客户卡。"}, status=404)
        return Response({**card_data(card), "type": "INTERNAL", "product": card.group.snapshot["product"],
                         "source_status": "NOT_LOADED", "url": f"/flow-cards/cards/{card.pk}"})

    def retrieve(self, request, pk=None):
        card = self.get_object()
        from .preparation import can_maintain
        whole = card.events.filter(kind="WHOLE_MOLD").order_by("-created_at").first()
        source = card.events.filter(kind="SOURCE_CONFIRM").order_by("-created_at").first()
        unresolved = card.events.filter(kind="ANOMALY").exclude(
            id__in=[e.values.get("anomaly_id") for e in card.events.filter(kind="RESOLUTION") if e.values.get("resolved") is True])
        measurements = list(card.events.filter(kind__in=s.MEASURE_STAGES).prefetch_related("cards", "cardevent_set").order_by("created_at", "id"))
        reviewed = reviewed_measurements(measurements)
        following = {e.replaces_id: e for e in measurements if e.replaces_id}
        alerts = []
        for e in measurements:
            if e.values.get("judgment") != "OUTSIDE" or e.pk in reviewed:
                continue
            target = e
            while target.pk in following:
                target = following[target.pk]
            alerts.append({**event_data(e), "card_no": card.card_no, "review_target": event_data(target)})
        latest = {}
        for e in sorted(measurements, key=lambda e: (e.business_at or e.created_at, e.created_at)):
            if e.pk in reviewed or e.values.get("review_effect") == "HISTORICAL_ONLY":
                continue
            latest[(e.kind, e.values.get("criterion_id"))] = e
        criteria = (card.group.snapshot.get("standard") or {}).get("snapshot", {}).get("criteria", [])
        status = []
        for criterion in applicable_criteria(criteria):
            # Coupon readings belong to an actual bake/sample, never an
            # unqualified all-card "latest normal" result.
            if criterion["stage"] == "BAKED_HARDNESS":
                continue
            e = latest.get((criterion["stage"], criterion["id"]))
            status.append({"criterion": criterion, "limit_text": limit_text(criterion),
                "event": event_data(e) if e else None,
                "message": e.values.get("measurement_message", "未测" if e.values.get("state") == "NOT_MEASURED" else "未知 / 未判定") if e else "未测 · 系统未登记"})
        bakes = list(card.events.filter(kind="BAKE").order_by("business_at", "created_at"))
        superseded_bakes = {e.replaces_id for e in bakes if e.replaces_id}
        bake_status = []
        for bake in bakes:
            if bake.pk in superseded_bakes:
                continue
            checks = []
            for sample in bake.values.get("coupon_samples", []):
                if str(card.pk) not in sample.get("card_ids", []):
                    continue
                readings = [e for e in measurements if e.kind == "BAKED_HARDNESS" and e.pk not in reviewed and e.values.get("review_effect") != "HISTORICAL_ONLY" and
                    e.values.get("bake_event_id") == str(bake.pk) and e.values.get("sample_ref") == sample["sample_ref"] and
                    e.values.get("material_ref") == sample["material_ref"]]
                readings.sort(key=lambda e: (e.business_at or e.created_at, e.created_at))
                checks.append({"sample_ref": sample["sample_ref"], "material_ref": sample["material_ref"], "trays": sample["trays"],
                    "message": readings[-1].values.get("measurement_message", "未判定") if readings else "本次胶片未测",
                    "actual": readings[-1].values.get("actual") if readings else None,
                    "unit": readings[-1].values.get("unit") if readings else None})
            card_range = next((r for r in bake.values.get("card_trays", []) if r["card_id"] == str(card.pk)), {})
            bake_status.append({"id": str(bake.pk), "business_at": bake.business_at, "scope": card_range.get("scope", bake.scope), "trays": card_range.get("trays", []), "checks": checks,
                "notice": "本次未登记代表本卡的随烤胶片，胶片烤后硬度未测；不沿用其他次结果" if not checks else "每次二烤按实际胶片分别检测，不沿用其他次结果"})
        product_readings = [e for e in measurements if e.kind == "BAKED_HARDNESS" and e.values.get("measurement_object") != "COUPON"]
        return Response({**card_data(card), "snapshot": card.group.snapshot, "write_enabled": settings.FLOW_CARDS_WRITE_ENABLED,
            "can_add_work_guidance": can_maintain(request.user),
            "whole_mold_status": "NOT_REQUIRED" if card.group.whole_mold == "NOT_REQUIRED" else whole.values.get("status") if whole else "SYSTEM_UNREGISTERED",
            "whole_mold_record": event_data(whole) if whole else None,
            "fixed_positions_notice": "定点只按每条原记录的实际范围解释；不取最后值、最大值或平均值作为整包统一值。",
            "source_status": "PHYSICAL_CONFIRMED" if source else "PLAN_ONLY",
            "source_confirmation": event_data(source) if source else None,
            "unresolved_anomalies": unresolved.count(), "measurement_alerts": alerts, "measurement_status": status, "bake_measurement_status": bake_status,
            "baked_product_measurements": [event_data(e) for e in product_readings],
            "baked_measurement_notice": "烤后检测按实际二烤、盘号和范围查看；没有唯一全包结果，胶片未测不代表成品未测。",
            "paper_status": "原图只表示已归档，不代表各工序完成或质量合格。"})

    @action(detail=True, methods=["get"])
    def events(self, request, pk=None):
        return page(self, request, self.get_object().events.prefetch_related("cards", "cardevent_set").order_by("created_at", "id"), event_data)

    @action(detail=True, methods=["get"], url_path="bake-samples")
    def bake_samples(self, request, pk=None):
        card = self.get_object()
        qs = card.events.filter(kind="BAKE").exclude(pk__in=CardEvent.objects.filter(replaces__isnull=False).values("replaces_id"))
        def eligible(event):
            data = event_data(event)
            data["values"] = {**event.values, "coupon_samples": [sample for sample in event.values.get("coupon_samples", [])
                if sample.get("accompanied") is True and str(card.pk) in sample.get("card_ids", [])]}
            return data
        return page(self, request, qs.prefetch_related("cards").order_by("-business_at", "-created_at"), eligible)

    @action(detail=True, methods=["post"], url_path="record")
    def record(self, request, pk=None):
        card = self.get_object()
        return Response(event_data(s.record_event(request.data, request.user, entry_card=card.pk)), status=201)

    @action(detail=True, methods=["get"])
    def attachments(self, request, pk=None):
        return page(self, request, self.get_object().attachments.order_by("-created_at"),
                    lambda a: {"id": str(a.pk), "created_at": a.created_at, "original_name": a.original_name,
                               "sha256": a.sha256, "replaces_id": str(a.replaces_id) if a.replaces_id else None,
                               "url": f"/api/flow-cards/attachments/{a.pk}/content/"})

    @action(detail=True, methods=["get"])
    def trace(self, request, pk=None):
        card = self.get_object()
        sources = card.shipment_sources.select_related("batch").order_by("created_at")
        def mapped(row):
            cases = source_returns(row)
            return {"id": row.pk, "batch_id": row.batch_id, "shipment_no": row.batch.shipment_no,
                "current_shipment_status": row.batch.status, "state": row.state, "scope": row.scope,
                "shipment_unit_no": row.shipment_unit_no, "snapshot": row.snapshot,
                "related_returns": [return_data(case) for case in cases[:20]], "related_return_count": cases.count(),
                "notice": "来源涉及集合，不等于每张卡承担全部退回数量或责任；实际分摊以原出货明细为准。"}
        return page(self, request, sources, mapped)

    @action(detail=True, methods=["get"], url_path="customer-inspection")
    def customer_inspection(self, request, pk=None):
        from orders.customer_inspection_views import printable_response
        card = self.get_object()
        sources = card.group.snapshot.get("customer_inspections", [])
        if not sources:
            raise Http404("发卡时未保存客户检验附件；请查看订单原表，不用现行资料冒充旧卡。")
        return printable_response(sources, f"flowcard-{card.pk}-customer-inspection.pdf")

    @action(detail=True, methods=["get"], url_path="trace-returns")
    def trace_returns(self, request, pk=None):
        source = get_object_or_404(self.get_object().shipment_sources, pk=s.integer(request.query_params.get("source_id"), "source_id", maximum=2147483647))
        return page(self, request, source_returns(source), return_data)


class EventView(ErrorsMixin, APIView):
    def post(self, request):
        return Response(event_data(s.record_event(request.data, request.user)), status=201)


class PrintViewSet(ErrorsMixin, viewsets.GenericViewSet):
    queryset = PrintJob.objects.all()

    @staticmethod
    def frozen_options(job):
        return {"second_inspection": bool(job.template.get("second_inspection")),
                "paper_layout": job.template.get("paper_layout", SINGLE)}

    def result(self, job):
        return {"id": str(job.pk), "status": job.status, "request_key": job.request_key,
                "items": [{"card_id": str(i.card_id), "group_id": str(i.card.group_id), "card_no": i.card.card_no, "sequence": i.card.sequence,
                           "denominator": i.card.denominator, "proposed_reprint": i.proposed_reprint,
                           "status": i.status, "active": i.active} for i in job.items.select_related("card").order_by("card__group_id", "card__sequence")],
                "pdf_url": f"/api/flow-cards/print-jobs/{job.pk}/pdf/", "pdf_sha256": job.pdf_sha256,
                "file_prepared": bool(job.pdf_storage_name), **self.frozen_options(job)}

    @action(detail=False, methods=["post"])
    def preview(self, request):
        from .pdf import render_cards
        options = s.print_options(request.data)
        source = s.print_cards(request.data, options)
        cards = [(SimpleNamespace(pk=c.pk, group_id=c.group_id, card_no=c.card_no, sequence=c.sequence,
                    denominator=c.denominator, planned_molds=c.planned_molds, original_date=c.original_date,
                    group=SimpleNamespace(snapshot={**c.group.snapshot, "paper_preview": True})),
                  c.reprint_count + (1 if c.printed_at else 0)) for c in source]
        try:
            raw = render_cards(cards, print_options=options)
        except (RuntimeError, ValueError) as exc:
            raise ValidationError({"detail": f"纸张预览生成失败，未建立打印任务：{exc}"}) from exc
        response = HttpResponse(raw, content_type="application/pdf")
        response["Content-Disposition"] = 'inline; filename="flowcard-paper-preview.pdf"'
        response["Cache-Control"] = "private, no-store"
        return response

    def prepare_file(self, job):
        if job.pdf_storage_name:
            return job
        if not job.items.filter(active=True).exists():
            raise ValidationError({"detail": "该打印任务已结束，不再生成文件；需要打印请从仍有效的原卡发起。"})
        from .pdf import render_cards
        from django.core.files.base import ContentFile
        items = list(job.items.select_related("card__group").order_by("card__group_id", "card__sequence"))
        # Rendering is deliberately OUTSIDE the quantity / print-reservation transaction.
        try:
            raw = render_cards([(i.card, i.proposed_reprint) for i in items], print_options=self.frozen_options(job))
        except (RuntimeError, ValueError) as exc:
            error = APIException({"detail": "PDF生成失败，打印任务仍保留；请使用原请求号重试。", "print_job_id": str(job.pk), "reason": str(exc)})
            error.status_code = 503
            raise error
        sha = hashlib.sha256(raw).hexdigest()
        try:
            name = private_storage().save(f"prints/{job.pk}-{sha}.pdf", ContentFile(raw))
        except OSError:
            return_error = APIException({"detail": "PDF保存失败，原打印任务仍保留；请核对存储后继续原任务。", "print_job_id": str(job.pk)})
            return_error.status_code = 503
            raise return_error
        # A competing retry may have won the same file preparation. Keep that
        # frozen file; never substitute a new renderer/font on a later GET.
        updated = PrintJob.objects.filter(pk=job.pk, pdf_storage_name="").update(pdf_storage_name=name, pdf_sha256=sha)
        if updated:
            s.audit(job, "PDF_FROZEN", self.request.user, {"sha256": sha})
        else:
            private_storage().delete(name)
        job.refresh_from_db()
        return job

    def list(self, request):
        qs = self.get_queryset().order_by("-created_at")
        if request.query_params.get("request_key"):
            qs = qs.filter(request_key=request.query_params["request_key"])
        item_scope = {}
        if request.query_params.get("card_id"):
            try:
                item_scope["items__card_id"] = uuid.UUID(request.query_params["card_id"])
            except ValueError:
                raise ValidationError({"card_id": "内部卡编号无效。"})
        if request.query_params.get("group_id"):
            try:
                item_scope["items__card__group_id"] = uuid.UUID(request.query_params["group_id"])
            except ValueError:
                raise ValidationError({"group_id": "卡组编号无效。"})
        if request.query_params.get("pending") == "1":
            item_scope["items__active"] = True
        # Apply together so pending must belong to the same selected card/group.
        return page(self, request, qs.filter(**item_scope).distinct(), self.result)

    def retrieve(self, request, pk=None):
        return Response(self.result(self.get_object()))

    def create(self, request):
        job = s.create_print(request.data, request.user)
        # The original request may be replayed after its task was cancelled.
        # Report the closed outcome even if it never reached PDF storage.
        return Response(self.result(job if job.status == "CLOSED" else self.prepare_file(job)), status=201)

    @action(detail=True, methods=["post"])
    def prepare(self, request, pk=None):
        s.write_enabled()
        return Response(self.result(self.prepare_file(self.get_object())))

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        return Response(self.result(s.confirm_print(self.get_object(), request.data, request.user)))

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        job = self.get_object()
        if not job.pdf_storage_name:
            return Response({"detail": "打印任务文件尚未生成；有权限人员请继续原任务生成，不另建补打。"}, status=503)
        try:
            result = FileResponse(private_storage().open(job.pdf_storage_name, "rb"), content_type="application/pdf",
                                  as_attachment=request.query_params.get("inline") != "1", filename=f"flowcards-{job.pk}.pdf")
        except FileNotFoundError:
            return Response({"detail": "原PDF文件缺失，请核对备份；不会用当前模板冒充原文件。"}, status=503)
        result["ETag"] = f'"{job.pdf_sha256}"'
        result["Cache-Control"] = "private, no-store"
        return result


class TemplateView(ErrorsMixin, APIView):
    def get(self, request):
        current = TemplatePublication.objects.filter(renderer=s.current_renderer()).first()
        return Response({"renderer": s.current_renderer(), "write_enabled": settings.FLOW_CARDS_WRITE_ENABLED,
            "version": current.version if current else "草稿",
            "template": s.current_template(), "published": bool(current),
            "history": list(TemplatePublication.objects.order_by("-version").values("renderer", "version", "confirmation", "created_at"))})

    def post(self, request):
        s.write_enabled()
        from . import preparation as prep
        if prep.enabled():
            prep.require_maintainer(request.user)
        confirmation = s.required_text(request.data, "confirmation")
        if request.data.get("renderer") != s.current_renderer() or request.data.get("print_confirmed") is not True:
            raise ValidationError({"confirmation": "请核对当前渲染版本并明确确认已按此版打印审核；预览和下载不会发布。"})
        with transaction.atomic():
            existing = TemplatePublication.objects.filter(renderer=s.current_renderer()).first()
            if not existing:
                existing = TemplatePublication.objects.create(renderer=s.current_renderer(),
                    version=(TemplatePublication.objects.aggregate(v=Max("version"))["v"] or 0) + 1,
                    confirmation=confirmation, created_by=request.user)
                s.audit(existing, "USER_TEMPLATE_RELEASE", request.user, {"confirmation": confirmation})
        return Response({"version": existing.version, "renderer": existing.renderer})


class TemplatePreviewView(ErrorsMixin, APIView):
    def get(self, request):
        # In-memory preview only: no issued identity, print counter or publication.
        from types import SimpleNamespace
        from django.utils import timezone
        from .pdf import render_cards
        standard = s.get_row(StandardRevision, request.query_params["standard_id"], "standard_id") if request.query_params.get("standard_id") else None
        product = standard.product if standard else None
        whole = request.query_params.get("whole_mold", "REQUIRED")
        if whole not in ("REQUIRED", "NOT_REQUIRED"):
            raise ValidationError({"whole_mold": "预览请选择需整模/不需整模。"})
        template = {**s.current_template(),
            "width_mm": s.integer(request.query_params.get("width_mm", 190), "width_mm", minimum=180, maximum=220),
            "height_mm": s.integer(request.query_params.get("height_mm", 130), "height_mm", minimum=125, maximum=165)}
        snapshot = {"template": template, "product": {"id": product.pk if product else None,
            "customer_product_no": product.customer_product_no if product else "隔离样例",
            "specification": product.specification if product else "预览样品（非真实要求）", "material": product.material if product else "待确认"},
            "orders": [{"id": None, "order_no": "PREVIEW-NOT-ISSUED", "item_no": "-", "customer": "预览"}],
            "whole_mold": whole, "standard": s.standard_data(standard) if standard else None,
            "appearance": standard.snapshot["appearance"] if standard else list(AppearanceIssue.objects.filter(is_active=True).values("id", "name")[:4])}
        if settings.FLOW_CARDS_PREPARATION_ENABLED and standard:
            from .paper_material import paper_material
            snapshot["route"] = standard.snapshot.get("route", {})
            snapshot["process_parameters"] = standard.snapshot.get("process_parameters", {})
            snapshot["paper_material"] = paper_material(product, compact=True)
        card = SimpleNamespace(pk=uuid.UUID(int=0), card_no="FC-PREVIEW-NOT-ISSUED", group=SimpleNamespace(snapshot=snapshot),
            original_date=timezone.localdate(), sequence=1, denominator=1, planned_molds=100)
        result = HttpResponse(render_cards([(card, 0)]), content_type="application/pdf")
        result["Content-Disposition"] = 'inline; filename="flowcard-draft-preview.pdf"'
        result["Cache-Control"] = "private, no-store"
        return result


def private_storage():
    return FileSystemStorage(location=settings.FLOW_CARDS_PRIVATE_ROOT)


class AttachmentView(ErrorsMixin, APIView):
    def post(self, request):
        s.write_enabled()
        upload = request.FILES.get("file")
        if not upload or upload.size > 12 * 1024 * 1024:
            raise ValidationError({"file": "请选择不超过12MB的原始图片。"})
        raw = upload.read()
        try:
            image = Image.open(io.BytesIO(raw))
            image.verify()
            if image.format not in ("JPEG", "PNG", "WEBP"):
                raise ValueError
        except (UnidentifiedImageError, ValueError, OSError):
            raise ValidationError({"file": "只支持有效的JPEG、PNG、WEBP图片，不接收SVG/脚本。"})
        card = s.get_row(InternalCard, request.data["card_id"], "card_id") if request.data.get("card_id") else None
        replaces = s.get_row(Attachment, request.data["replaces_id"], "replaces_id") if request.data.get("replaces_id") else None
        if replaces and replaces.card_id != (card.pk if card else None):
            raise ValidationError({"replaces_id": "不能替换另一张卡的原图。"})
        sha = hashlib.sha256(raw).hexdigest()
        existing = Attachment.objects.filter(card=card, sha256=sha).first()
        if existing:
            return Response({"id": str(existing.pk), "deduplicated": True})
        from django.core.files.base import ContentFile
        with transaction.atomic():
            if card:
                # Serialize attachment creation with the unused-card check.
                card = InternalCard.objects.select_for_update().get(pk=card.pk)
                if card.status != "ACTIVE":
                    raise ValidationError({"card_id": "已作废的卡只供追溯，不能新增照片。"})
            name = private_storage().save(f"{uuid.uuid4().hex}.bin", ContentFile(raw))
            row = Attachment.objects.create(card=card, storage_name=name, original_name=Path(upload.name).name[:255], sha256=sha,
                content_type={"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}[image.format],
                replaces=replaces, created_by=request.user)
            s.audit(row, "ARCHIVE_ORIGINAL", request.user, {"sha256": sha, "replaces_id": str(replaces.pk) if replaces else None})
        return Response({"id": str(row.pk)}, status=201)


class AttachmentContentView(ErrorsMixin, APIView):
    def get(self, request, pk):
        row = get_object_or_404(Attachment, pk=pk)
        try:
            result = FileResponse(private_storage().open(row.storage_name, "rb"), content_type=row.content_type)
        except FileNotFoundError:
            raise Http404("原始图片缺失；不能据此恢复手写内容。")
        result["Cache-Control"] = "private, no-store"
        result["X-Content-Type-Options"] = "nosniff"
        return result


class DiagramView(ErrorsMixin, APIView):
    def post(self, request):
        from molds.models import MoldAsset
        s.write_enabled()
        product = s.get_row(ProductSpecification, request.data.get("product_id"), "product_id")
        mold = s.get_row(MoldAsset, request.data.get("mold_id"), "mold_id")
        attachment = s.get_row(Attachment, request.data.get("attachment_id"), "attachment_id")
        with transaction.atomic():
            ProductSpecification.objects.select_for_update().get(pk=product.pk)
            version = (PositionDiagram.objects.filter(product=product, mold=mold).aggregate(v=Max("version"))["v"] or 0) + 1
            row = PositionDiagram.objects.create(product=product, mold=mold, attachment=attachment, version=version,
                direction=s.required_text(request.data, "direction", 300), scope=s.required_text(request.data, "scope"),
                positions=s.required_text(request.data, "positions"), created_by=request.user)
            s.audit(row, "CONFIRM_DIAGRAM", request.user, {"attachment": str(attachment.pk)})
        return Response({"id": row.pk, "version": row.version}, status=201)

    def get(self, request):
        row = s.get_row(PositionDiagram, request.query_params.get("id"), "id")
        return Response({"id": row.pk, "version": row.version, "direction": row.direction, "scope": row.scope,
                         "positions": row.positions, "product_id": row.product_id, "mold_id": row.mold_id,
                         "url": f"/api/flow-cards/attachments/{row.attachment_id}/content/"})


class ShipmentSourcesView(ErrorsMixin, APIView):
    def get(self, request, pk):
        batch = get_object_or_404(QualityShipmentBatch, pk=pk)
        return page(self, request, batch.internal_sources.select_related("card").order_by("id"),
            lambda r: {"id": r.pk, "card_id": str(r.card_id), "card_no": r.card.card_no, "scope": r.scope,
                       "state": r.state, "shipment_unit_no": r.shipment_unit_no, "snapshot": r.snapshot})

    def post(self, request, pk):
        batch = get_object_or_404(QualityShipmentBatch, pk=pk)
        rows = s.bind_sources(batch, request.data.get("sources"), request.user)
        return Response({"ids": [row.pk for row in rows], "quantity_effect": "NONE"})


class ShipmentSourceReviewView(ErrorsMixin, APIView):
    def post(self, request, pk):
        return Response(s.review_source(get_object_or_404(ShipmentSource, pk=pk), request.data, request.user))
