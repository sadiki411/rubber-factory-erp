"""Conservative extraction into the existing product's revision editor, never a commit.

Recognises labelled fields/table headings rather than customer names or fixed cells.
Raw requirements and their sheet/row survive even when they cannot be interpreted.
"""
import io
import hashlib
import re
from zipfile import ZipFile
from decimal import Decimal
from openpyxl import load_workbook
from rest_framework.exceptions import ValidationError


NUMBER = r"\d+(?:\.\d+)?"


def text(value):
    return "" if value is None else str(value).strip()


def limits(raw):
    value = text(raw).replace("−", "-").replace("～", "~").replace("–", "-")
    result = {"lower": "", "upper": "", "limit_mode": "PENDING", "lower_inclusive": True, "upper_inclusive": True}
    asymmetric = re.search(rf"({NUMBER})\s*\+({NUMBER})\s*/\s*-({NUMBER})", value)
    tolerance = re.search(rf"({NUMBER})\s*[±]\s*({NUMBER})", value)
    interval = re.search(rf"({NUMBER})\s*[~-]\s*({NUMBER})", value)
    side = re.search(rf"(≤|>=|≥|<=|<|>)\s*({NUMBER})", value)
    if asymmetric:
        center, up, down = map(Decimal, asymmetric.groups())
        result.update(lower=str(center-down), upper=str(center+up), limit_mode="RANGE")
    elif tolerance:
        center, delta = map(Decimal, tolerance.groups())
        result.update(lower=str(center-delta), upper=str(center+delta), limit_mode="RANGE")
    elif interval:
        result.update(lower=interval[1], upper=interval[2], limit_mode="RANGE")
    elif side:
        upper = side[1] in ("≤", "<=", "<")
        result.update({"upper" if upper else "lower": side[2], "limit_mode": "UPPER" if upper else "LOWER",
                       "upper_inclusive" if upper else "lower_inclusive": side[1] not in ("<", ">")})
    return result


IDENTITY = {"客户": "customer_key", "客户名称": "customer_key", "客户料号": "customer_product_no", "客户产品编号": "customer_product_no", "客户产品号": "customer_product_no", "规格": "specification", "材料": "material", "材质": "material", "模具": "mold_no", "模具编号": "mold_no", "模具号": "mold_no", "版次": "version", "文件版号": "version", "版本": "version", "订单号": "order_no", "项次": "item_no", "适用范围": "scope", "依据产品版本": "base_version"}
PARAMS = {"上模设定温度": "upper_set_temperature", "下模设定温度": "lower_set_temperature", "上模实测目标": "upper_contact_temperature", "下模实测目标": "lower_contact_temperature", "实际温度上模": "upper_contact_temperature", "实际温度下模": "lower_contact_temperature", "一次硫化时间": "primary_curing_time", "一次加硫时间": "primary_curing_time", "压力": "pressure", "脱模剂": "release_agent", "离型剂": "release_agent", "洒油": "oil", "毛边洒油": "oil", "排气": "venting", "机型": "machine_type", "吨位": "machine_type"}
LABELS = set(IDENTITY) | set(PARAMS) | {"硬度", "线径", "内径", "二烤", "线径测量", "内径测量", "备注"}
IDENTITY["依据的产品版本"] = "base_version"
LABELS.add("依据的产品版本")


def preview_workbook(content):
    try:
        with ZipFile(io.BytesIO(content)) as archive:
            if sum(i.file_size for i in archive.infolist()) > 50 * 1024 * 1024:
                raise ValueError("Workbook decompressed size limit")
        book = load_workbook(io.BytesIO(content), data_only=False, read_only=True)
    except Exception as exc:
        raise ValidationError({"file": "无法读取工作簿，请上传有效的xlsx文件。"}) from exc
    candidates = []
    try:
        if len(book.worksheets) > 30:
            raise ValidationError({"file": "工作表过多，请分批上传（最多30张）。"})
        for sheet in book.worksheets:
            if (sheet.max_row or 0) > 3000 or (sheet.max_column or 0) > 80:
                raise ValidationError({"file": "标准表过大，请按产品分批上传。"})
            rows = []
            for n, row in enumerate(sheet.iter_rows(values_only=True)):
                if n >= 3000 or len(row) > 80:
                    raise ValidationError({"file": "标准表过大，请按产品分批上传。"})
                rows.append([text(c) for c in row])
            item = {"sheet": sheet.title, "criteria": [], "process_parameters": {}, "hardness": {}, "bake": {"state": "UNKNOWN"}, "warnings": [], "raw_rows": rows, "source_kind": "CUSTOMER", "whole_mold": "UNKNOWN"}
            # Label/value pairs and adjacent horizontal header/data blocks.
            for i, row in enumerate(rows):
                headings = [j for j, v in enumerate(row) if v in IDENTITY]
                wide = any(v in LABELS and j+1 < len(row) and row[j+1] in LABELS for j,v in enumerate(row))
                for j in headings:
                    value = rows[i+1][j] if wide and i+1 < len(rows) and j < len(rows[i+1]) else row[j+1] if j+1 < len(row) else ""
                    if value and value not in IDENTITY:
                        item[IDENTITY[row[j]]] = value
            entries = []
            for i, row in enumerate(rows):
                if "原项目" in row or "项目" in row and ("原要求" in row or "订单补充要求" in row):
                    mapping = {v.replace("／", "/"):j for j,v in enumerate(row)}
                    for n in range(i+1, len(rows)):
                        values = rows[n]
                        get = lambda *names: next((values[mapping[name]] for name in names if name in mapping and mapping[name] < len(values)), "")
                        name, raw = get("原项目", "项目"), get("原要求", "订单补充要求")
                        if name and raw:
                            entries.append((n+1, name, raw, get("工序"), get("单位/量表", "单位"), get("对象/方法"), get("执行说明", "备注")))
                # Horizontal property block: preserve values by exact labelled column.
                if sum(v in LABELS for v in row) >= 2 and any(v in ("硬度", "线径", "内径", "一次硫化时间") for v in row) and i+1 < len(rows):
                    for j, name in enumerate(row):
                        if name in ("硬度", "线径", "内径", "二烤", *PARAMS):
                            raw = rows[i+1][j] if j < len(rows[i+1]) else ""
                            if raw:
                                notes = " ".join(rows[n+1][k] for n,r in enumerate(rows[:-1]) for k,c in enumerate(r) if name+"测量" == c and k<len(rows[n+1]))
                                entries.append((i+2, name, raw, "", "", "", notes))
            for row_no, name, raw, stage, unit, method, note in entries:
                source = {"sheet": sheet.title, "row": row_no, "raw_text": raw, "original_item": name, "execution_note": note}
                if raw.startswith("="):
                    item["warnings"].append(f"第{row_no}行是公式，保留原文待核，不计算限值。")
                if name in PARAMS:
                    parsed = limits(raw)
                    found_unit = unit if unit not in ("", "—", "见原要求") else next((u for u in ("min", "秒", "s", "°C", "℃", "kg/cm²", "MPa", "T") if u in raw), "")
                    item["process_parameters"][PARAMS[name]] = {"state": "RAW_UNCONFIRMED", "lower": parsed["lower"], "upper": parsed["upper"], "unit": found_unit, "raw_text": raw, "source": f"{sheet.title} 第{row_no}行；{method}"}
                elif "二烤" in name:
                    match = re.search(rf"({NUMBER})\s*(?:°C|℃)\s*[×xX*]\s*({NUMBER})\s*min", raw)
                    if match:
                        item["bake"] = {"state": "APPLICABLE", "temperature_lower": match[1], "temperature_upper": match[1], "minutes_lower": match[2], "minutes_upper": match[2]}
                    else:
                        item["warnings"].append(f"第{row_no}行二烤条件不完整，仍为待确认，不以0补齐。")
                    item["process_parameters"]["bake_reference"] = {"state": "RAW_UNCONFIRMED", "raw_text": raw, "source": f"{sheet.title} 第{row_no}行"}
                elif name in ("线径", "内径") or "硬度" in name or name.upper().startswith("HD"):
                    parsed = limits(raw)
                    dimension = name in ("线径", "内径")
                    phase = "DIMENSION" if dimension else "BAKED_HARDNESS" if "二次" in stage or "烤" in stage else "MOLDING_HARDNESS"
                    actual_unit = unit if unit not in ("—", "见原要求") else ""
                    if not actual_unit:
                        actual_unit = "mm" if re.search(r"\bmm\b", raw) else ""
                    measure = "不随包" not in note and (name != "内径" or "随包加测" in raw or "随包测" in note)
                    if not dimension:
                        item["hardness"][phase] = {"object": "COUPON" if "胶片" in method else "PRODUCT" if "成品" in method else "UNKNOWN", "scale": actual_unit, "instrument": method, "accepted_method": method}
                    criterion = {**parsed, "name": name, "stage": phase, "unit": actual_unit, "method": method, "requirement_state": "RAW_UNCONFIRMED", "critical": measure, "follow_package": measure, "provenance": source}
                    if "加测" in raw:
                        criterion["inherit_base"] = True
                    item["criteria"].append(criterion)
                    if parsed["limit_mode"] == "PENDING" or not actual_unit or not method:
                        item["warnings"].append(f"第{row_no}行{name}：界限、单位或测法未完整，需人工核对。")
            if item.get("customer_product_no") and (entries or item.get("order_no")):
                item["source"] = f"{sheet.title} / {item.get('version') or item.get('base_version') or '版号未提供'}"
                item["scope"] = item.get("scope", "")
                item["route"] = {"confirmed": False, "molding": True, "patrol": True, "deburr": True, "inspection": True, "bake": item["bake"]["state"] == "APPLICABLE", "baked_hardness": "BAKED_HARDNESS" in item["hardness"]}
                candidates.append(item)
        if not candidates:
            raise ValidationError({"file": "没有识别到带客户料号的标准块。请使用界面的手工填写入口，或提供带项目/要求列的标准表。"})
        # Explicit order exceptions inherit only a unique same-product document version.
        for item in candidates:
            if not item.get("order_no"):
                continue
            bases = [b for b in candidates if not b.get("order_no") and b.get("customer_product_no") == item.get("customer_product_no") and b.get("version") == item.get("base_version")]
            if len(bases) == 1:
                from copy import deepcopy
                base = bases[0]
                for key in ("customer_key", "specification", "material", "mold_no", "hardness", "bake", "process_parameters", "route"):
                    item[key] = deepcopy(base.get(key))
                overrides = {c["name"]: c for c in item["criteria"]}
                merged = []
                for original in base["criteria"]:
                    change = overrides.pop(original["name"], None)
                    if change and change.get("inherit_base"):
                        criterion = deepcopy(original)
                        criterion.update(follow_package=True, critical=True, provenance=change["provenance"])
                    elif change:
                        criterion = {**deepcopy(original), **change, "unit": change["unit"] or original["unit"], "method": change["method"] or original["method"]}
                    else:
                        criterion = deepcopy(original)
                    merged.append(criterion)
                item["criteria"] = merged + list(overrides.values())
            else:
                item["warnings"].append("订单补充未找到唯一基础版本，必须人工核对，不猜配。")
        for item in candidates:
            item["import_source"] = {"sha256": hashlib.sha256(content).hexdigest(), "sheet": item["sheet"], "raw_rows": item["raw_rows"],
                                     "identity": {k: item.get(k, "") for k in ("customer_product_no", "specification", "material", "mold_no", "customer_key", "version", "order_no", "item_no")}}
        return {"candidates": candidates, "notice": "仅识别预览，尚未修改产品。请逐项确认已有产品、客户及版本，再保存。"}
    finally:
        book.close()
