"""Advisory import-time mold checks. Never move molds or block order import."""

from collections import Counter, defaultdict

from django.utils import timezone

from molds.models import MoldAsset, MoldModel
from .models import BusinessRecordRevision, ProductSpecification
from .services import model_snapshot, record_revision


def _groups(records, products_by_source=None):
    products_by_source = products_by_source or {}
    products = {row["source_key"]: row for row in records if row["record_type"] == "PRODUCT_SPECIFICATION"}
    groups = {}
    for order in records:
        if order["record_type"] != "ORDER":
            continue
        source = order.get("product_spec_source_key")
        product = products.get(source, {})
        created = products_by_source.get(source)
        product_id = created.pk if created else product.get("match_id") or order.get("product_spec_id")
        key = f"source:{source}" if source else f"product:{product_id}" if product_id else f"order:{order['row_key']}"
        group = groups.setdefault(key, {
            "binding_key": key, "product_id": product_id,
            "specification": order.get("specification", ""), "material": order.get("material", ""),
            "customer_mold_no": product.get("mold_no", ""),
            "customer_model_id": product.get("mold_model_id"),
            "can_bind": bool(product_id or (product and product.get("action") == "CREATE")),
            "orders": [],
        })
        group["orders"].append({"order_no": order.get("order_no", ""), "item_no": order.get("item_no", ""),
                                "sheet": order.get("sheet", ""), "row": order.get("row")})
    return list(groups.values())


def build_mold_check(records, selections=None, *, products_by_source=None, include_options=True):
    groups = _groups(records, products_by_source)
    if not groups:
        return None
    selections = selections or {}
    products = ProductSpecification.objects.filter(pk__in={row["product_id"] for row in groups if row["product_id"]}).in_bulk()
    models = MoldModel.objects.in_bulk()
    assets_by_model = defaultdict(list)
    for asset in MoldAsset.objects.filter(is_active=True).select_related("current_slot", "current_machine").order_by("asset_code", "id"):
        assets_by_model[asset.mold_model_id].append({
            "id": asset.pk, "asset_code": asset.asset_code, "status": asset.status,
            "status_display": asset.get_status_display(),
            "location": asset.current_slot.display_code if asset.current_slot_id else
                        asset.current_machine.code if asset.current_machine_id else "客户收回",
        })
    rows = []
    for group in groups:
        product = products.get(group["product_id"])
        remembered_id = product.mold_model_id if product else None
        selected_id = selections.get(group["binding_key"])
        model_id = selected_id or remembered_id or group["customer_model_id"]
        model = models.get(model_id)
        source = "MANUAL" if selected_id else "REMEMBERED" if remembered_id else "CUSTOMER_MATCH" if model_id else "UNLINKED"
        assets = assets_by_model.get(model_id, [])
        counts = Counter(asset["status"] for asset in assets)
        can_bind = group["can_bind"] and (product is None or product.is_active)
        if not can_bind:
            status, message = "UNRESOLVED_PRODUCT", "产品档案尚未唯一确定或已停用，不能安全记住模具关联；不会猜配。"
        elif not model_id:
            status, message = "UNLINKED", "尚未关联模具型号，可搜索后选择，也可暂不关联继续导入。"
        elif model is None:
            status, message = "MODEL_MISSING", "所选模具型号已不存在，关联不会保存；订单仍可导入，请重新核对。"
        elif not model.is_active:
            status, message = "MODEL_INACTIVE", "关联型号已停用，请核对；不会自动替换，也不阻止订单导入。"
        elif counts["IN_STOCK"]:
            status, message = "IN_STOCK", f"在库 {counts['IN_STOCK']} 副，上机 {counts['ON_MACHINE']} 副，客户收回 {counts['OUTSOURCED']} 副。"
        elif counts["ON_MACHINE"]:
            status, message = "ON_MACHINE", f"模具在厂，正在上机占用 {counts['ON_MACHINE']} 副，不算缺模。"
        elif assets:
            status, message = "CUSTOMER_RETURNED", "该型号启用的实物模具均为客户收回，请确认返厂安排；仍可导入。"
        else:
            status, message = "NO_ASSET", "已找到模具型号，但未找到启用的实物模具档案；不等于确认实物不存在，仍可导入。"
        source_conflict = bool(remembered_id and group["customer_model_id"] and group["customer_model_id"] != remembered_id and not selected_id)
        customer_number_changed = bool(product and product.mold_no and group["customer_mold_no"] and product.mold_no != group["customer_mold_no"])
        notices = []
        if source_conflict:
            notices.append("客户模具号匹配到另一型号，已保留原关联，请人工核对。")
        if customer_number_changed:
            notices.append("本次客户原表模具号与档案原值不同，请核对关联；不会自动覆盖已有模具型号。")
        rows.append({**group, "can_bind": can_bind, "mold_model_id": model_id,
                     "mold_model_code": model.code if model else "", "selection_source": source,
                     "remembered_model_id": remembered_id, "status": status, "message": message,
                     "assets": assets, "notices": notices,
                     "needs_attention": status not in ("IN_STOCK", "ON_MACHINE") or bool(notices)})
    result = {"version": 1, "checked_at": timezone.now().isoformat(), "rows": rows,
              "alert_count": sum(row["needs_attention"] for row in rows)}
    if include_options:
        result["model_options"] = [{
            "id": model.pk, "code": model.code, "product_name": model.product_name,
            "is_active": model.is_active,
            "in_stock": sum(asset["status"] == "IN_STOCK" for asset in assets_by_model[model.pk]),
            "on_machine": sum(asset["status"] == "ON_MACHINE" for asset in assets_by_model[model.pk]),
            "returned": sum(asset["status"] == "OUTSOURCED" for asset in assets_by_model[model.pk]),
        } for model in sorted(models.values(), key=lambda item: (item.code, item.pk))]
    return result


def update_mold_selection(records, selections, binding_key, model_id):
    groups = {row["binding_key"]: row for row in _groups(records)}
    if not isinstance(binding_key, str) or binding_key not in groups:
        raise ValueError("产品关联项已变化，请重新核对模具；原订单预检仍保留。")
    if not groups[binding_key]["can_bind"]:
        raise ValueError("产品身份尚未确定，不能猜配模具；可暂不关联继续导入。")
    updated = dict(selections)
    if model_id is None:
        updated.pop(binding_key, None)  # Undo this draft choice, not the saved product association.
        return updated
    if type(model_id) is not int or not MoldModel.objects.filter(pk=model_id, is_active=True).exists():
        raise ValueError("请选择启用的模具型号；查不到时可暂不关联继续导入。")
    target_id = groups[binding_key]["product_id"]
    for key, other in groups.items():
        if target_id and key != binding_key and other["product_id"] == target_id and updated.get(key) not in (None, model_id):
            raise ValueError("同一产品在本批次选择了不同模具型号，请统一选择；原预检仍保留。")
    updated[binding_key] = model_id
    return updated


def apply_mold_selections(records, selections, products_by_source, *, user, batch):
    """Apply explicit choices only as part of the successful import transaction."""
    if not selections:
        return 0, []
    groups = _groups(records, products_by_source)
    original_targets = {row["binding_key"]: row["product_id"] for row in _groups(records)}
    targets = defaultdict(set)
    warnings = []
    baselines = batch.payload.get("mold_selection_baselines", {})
    expected_by_product = defaultdict(list)
    for group in groups:
        model_id = selections.get(group["binding_key"])
        if model_id and group["can_bind"] and group["product_id"]:
            targets[group["product_id"]].add(model_id)
            expected_by_product[group["product_id"]].append(baselines.get(group["binding_key"], {}))
            if group["binding_key"] in baselines and baselines[group["binding_key"]].get("product_id") != original_targets.get(group["binding_key"]):
                expected_by_product[group["product_id"]].append({"product_id": -1})
    models = MoldModel.objects.select_for_update().filter(pk__in={mid for values in targets.values() for mid in values}, is_active=True).in_bulk()
    products = ProductSpecification.objects.select_for_update().filter(pk__in=targets, is_active=True).in_bulk()
    changed = 0
    for product_id, model_ids in targets.items():
        product = products.get(product_id)
        model_id = next(iter(model_ids)) if len(model_ids) == 1 else None
        if product is None or model_id not in models:
            warnings.append("产品或所选模具型号已变化，相关人工关联未保存；订单导入不因此中止，请在导入历史核对。")
            continue
        if any(expected.get("product_id") is not None and (
            expected["product_id"] != product.pk or expected.get("mold_model_id") != product.mold_model_id
        ) for expected in expected_by_product[product_id]):
            warnings.append("产品的已保存模具关联在预检后发生变化，已保留最新关联、未覆盖；订单已导入，请另行核对。")
            continue
        if product.mold_model_id == model_id:
            continue
        before = model_snapshot(product)
        product.mold_model_id = model_id
        product.save()
        record_revision(product, user, BusinessRecordRevision.Action.IMPORT, source_batch=batch, before=before)
        changed += 1
    return changed, warnings
