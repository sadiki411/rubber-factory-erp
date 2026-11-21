"""Narrow, SHA-addressed supplement for already committed orders. No reimport."""
from collections import defaultdict
import re

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from orders import customer_inspection as ci
from orders.models import BusinessImportBatch, OrderInspectionSource, ProductInspectionCriterion
from quality.models import QualityOrder


class Command(BaseCommand):
    help = "补齐指定已导入翔邦文件的客户代号与标准候选；默认仅核对，不重新导订单，不确认标准。"

    def add_arguments(self, parser):
        parser.add_argument("--sha", action="append", required=True)
        parser.add_argument("--expect-orders", type=int, required=True)
        parser.add_argument("--expect-criteria", type=int, required=True)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        shas = options["sha"]
        if len(shas) != len(set(shas)) or any(not re.fullmatch("[0-9a-f]{64}", v) for v in shas):
            raise CommandError("请提供不重复的原文件 SHA-256。")
        with transaction.atomic():
            prepared = []
            seen = set()
            count = 0
            for sha in shas:
                matches = list(BusinessImportBatch.objects.filter(
                    sha256=sha, source_type=ci.FACTORY, status=BusinessImportBatch.Status.COMMITTED,
                )[:2])
                if len(matches) != 1:
                    raise CommandError(f"文件 {sha[:12]} 没有唯一的已导入批次，未修改。")
                batch = matches[0]
                rows = batch.payload.get("rows", [])
                grouped = defaultdict(list)
                for row in rows:
                    if row["record_type"] == "INSPECTION_CRITERION":
                        grouped[row["order_source_key"]].append(row)
                for r in (r for r in rows if r["record_type"] == "ORDER"):
                    order = QualityOrder.objects.filter(
                        external_key=r["external_key"], order_no=r["order_no"], item_no=r["item_no"],
                    ).first()
                    criteria = grouped.pop(r["source_key"], [])
                    if (not order or order.pk in seen or not criteria or r.get("stale_source_version")
                            or not order.product_specification_id or order.source_system != r["source_system"]
                            or order.specification != r["specification"] or order.material != r["material"]
                            or batch.pk not in (order.source_batch_id, order.last_source_batch_id)):
                        raise CommandError(f"{batch.original_name} 第 {r['row']} 行订单已变更、重复或资料不足，未修改。")
                    snapshot = ci.build_snapshot(ci.imported_rows(criteria, batch), r["raw_data"])
                    stored = ProductInspectionCriterion.objects.filter(order=order, product_specification_id=order.product_specification_id,
                        source_batch=batch).values("customer", "project_no", "category", "version", "inspection_item", "lower_limit", "upper_limit", "unit")
                    if (not snapshot["digest"] or ci.build_snapshot(stored, {})["digest"] != snapshot["digest"]
                            or len(stored) != len(criteria) or (order.customer_code and order.customer_code != snapshot["customer_code"])):
                        raise CommandError(f"{batch.original_name} 第 {r['row']} 行原要求有冲突或已变更，未修改。")
                    snapshot["product_id"] = order.product_specification_id
                    existing = OrderInspectionSource.objects.filter(order=order).first()
                    if existing and (existing.document_id != batch.pk or existing.snapshot != snapshot):
                        raise CommandError(f"{batch.original_name} 第 {r['row']} 行有不同的现行来源，未覆盖。")
                    prepared.append((order, batch, snapshot, existing))
                    seen.add(order.pk)
                    count += len(criteria)
                if grouped:
                    raise CommandError(f"{batch.original_name} 有检验记录未匹配订单，未修改。")
            if len(prepared) != options["expect_orders"] or count != options["expect_criteria"]:
                raise CommandError("数量与指定预期不符，未修改。")
            added = 0
            if options["apply"]:
                for order, batch, snapshot, existing in prepared:
                    if not existing:
                        OrderInspectionSource.objects.create(order=order, document=batch, snapshot=snapshot)
                        added += 1
                    QualityOrder.objects.filter(pk=order.pk, customer_code="").update(customer_code=snapshot["customer_code"])
            operation = "已补充" if options["apply"] else "只读预核对"
            self.stdout.write(f"{operation}：{len(prepared)} 个订单项次、{count} 条原始检验、新增候选 {added}；未修改订单数量、状态、历史卡或标准生效状态。")
