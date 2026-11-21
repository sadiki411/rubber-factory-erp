"""Read-only operational guidance. References are not confirmed instructions or facts."""
import re
from decimal import Decimal, InvalidOperation
from rest_framework.exceptions import ValidationError

LABELS = {
    "oil": "毛边洒油", "upper_contact_temperature": "上模接触温度目标", "lower_contact_temperature": "下模接触温度目标",
    "upper_set_temperature": "上模设定温度", "lower_set_temperature": "下模设定温度", "primary_curing_time": "一次加硫时间",
    "pressure": "压力", "release_agent": "成型脱模剂", "venting": "排气要求", "arrangement": "排列/排料", "experience": "生产注意事项",
}
NUMERIC = {"upper_contact_temperature", "lower_contact_temperature", "upper_set_temperature", "lower_set_temperature", "primary_curing_time", "pressure"}


def same_value(a, b):
    try:
        return a not in (None, "") and b not in (None, "") and Decimal(str(a)) == Decimal(str(b))
    except InvalidOperation:
        return a == b


def parameter_text(p):
    if p.get("value") not in (None, ""):
        value = str(p["value"])
    elif same_value(p.get("lower"), p.get("upper")):
        value = str(p["lower"])
    elif p.get("lower") not in (None, "") and p.get("upper") not in (None, ""):
        value = f"{p['lower']}～{p['upper']}"
    elif p.get("lower") not in (None, ""):
        value = f"≥{p['lower']}"
    elif p.get("upper") not in (None, ""):
        value = f"≤{p['upper']}"
    else:
        return p.get("raw_text") or "未提供"
    return value + (f" {p['unit']}" if p.get("unit") else "")


def bake_text(bake):
    if bake.get("state") == "NOT_APPLICABLE":
        return "无需二烤"
    if bake.get("state") != "APPLICABLE":
        return "二烤要求待确认"
    def part(prefix, unit):
        return parameter_text({"value": bake.get(prefix + "_value"), "lower": bake.get(prefix + "_lower"), "upper": bake.get(prefix + "_upper"), "unit": unit})
    return f"{part('temperature', '℃')} × {part('minutes', 'min')}"


def usable(key, param):
    return param.get("state") == "CONFIRMED" and (key not in NUMERIC or bool(param.get("unit")))


def oil_instruction(param):
    text = parameter_text(param)
    if param.get("state") == "NOT_APPLICABLE":
        return "洒油：确认无要求（不等于已执行）"
    if usable("oil", param) and text != "未提供":
        if text.strip() in ("禁止", "禁油", "禁止洒油", "不洒油", "不使用"):
            return "要求：禁止洒油"
        return f"洒油要求：{text}"
    if text == "未提供" or param.get("state") == "NOT_PROVIDED":
        return "洒油要求未提供，作业前找负责人确认"
    return f"洒油要求待核，作业前找负责人确认；候选原文：{text}（非指令）"


def summary(product, standard=None, snapshot=None):
    ss = snapshot or (standard.snapshot if standard else {})
    params = ss.get("process_parameters", {})
    rows = []
    for key, label in LABELS.items():
        param = params.get(key, {})
        if not param and key not in ("oil", "primary_curing_time", "pressure", "upper_contact_temperature", "lower_contact_temperature"):
            continue
        state = param.get("state", "NOT_PROVIDED")
        warning = ""
        if key in NUMERIC and state != "NOT_APPLICABLE" and any(param.get(f) not in (None, "") for f in ("value", "lower", "upper")) and not param.get("unit"):
            warning = "缺少单位，不作为可执行指令，找产品标准负责人核对"
        elif state not in ("CONFIRMED", "NOT_APPLICABLE"):
            warning = "未提供" if state == "NOT_PROVIDED" else "原文待核，不作为生产指令"
        raw = product.primary_curing if key == "primary_curing_time" else ""
        if raw and re.search(r"(?<![a-z])min(?![a-z])|分钟", raw, re.I):
            warning = "原始资料含min/分钟，请核原单单位，不自动换算为秒"
            if param.get("unit", "").lower() in ("s", "sec", "秒"):
                warning = "单位冲突：原文min/分钟，所选版为秒；找产品标准负责人核原单，不自动换算"
        rows.append({"key": key, "label": label, "value": parameter_text(param), "state": state,
                     "instruction": oil_instruction(param) if key == "oil" else "", "raw_reference": raw,
                     "warning": warning, "owner": "产品标准负责人 / 管理员"})
    criteria = ss.get("criteria", [])
    stages = [("MOLDING_HARDNESS", "成型硬度"), ("DIMENSION", "线径/尺寸界限")]
    if ss.get("route", {}).get("baked_hardness"):
        stages.append(("BAKED_HARDNESS", "烤后硬度"))
    for stage, label in stages:
        matching = [c for c in criteria if c.get("stage") == stage]
        if not matching or all(c.get("requirement_state") == "NOT_PROVIDED" or c.get("limit_mode") == "PENDING" for c in matching):
            rows.append({"key": stage, "label": label, "value": "未提供", "state": "NOT_PROVIDED", "warning": "找产品标准负责人核原标准，不借用其他产品数据", "owner": "产品标准负责人 / 管理员"})
        else:
            from .limits import limit_text
            for c in matching:
                rows.append({"key": f"{stage}:{c['id']}", "label": label if stage == "BAKED_HARDNESS" else c.get("name") or label, "value": limit_text(c), "state": c.get("requirement_state", "RAW_UNCONFIRMED"),
                             "warning": "原文待核，不作为操作界限" if c.get("requirement_state") in ("RAW_UNCONFIRMED", "NOT_PROVIDED") else "", "owner": "产品标准负责人 / 管理员"})
    bake = ss.get("bake", {})
    rows.append({"key": "bake", "label": "二次加硫", "value": bake_text(bake), "state": "CONFIRMED" if bake.get("state") in ("APPLICABLE", "NOT_APPLICABLE") else "RAW_UNCONFIRMED",
                 "raw_reference": params.get("bake_reference", {}).get("raw_text") or product.secondary_curing, "warning": "参考原文待确认，不等于本批已二烤" if bake.get("state") not in ("APPLICABLE", "NOT_APPLICABLE") else "", "owner": "产品标准负责人 / 管理员"})
    return rows


def temperature_plan(value):
    if value is None:
        value = {"mode": "PENDING"}
    if not isinstance(value, dict) or value.get("mode") not in ("PENDING", "FIRST", "EVERY", "REFERENCE"):
        raise ValidationError({"temperature_plan": "请选择待确认、首卡/换模调机异常复测、每包实测或引用实际测温记录。"})
    result = {"mode": value["mode"]}
    for key in ("basis", "reference"):
        text = value.get(key, "")
        if not isinstance(text, str) or len(text) > 500:
            raise ValidationError({"temperature_plan": "测温依据/引用每项最多500字。"})
        result[key] = text.strip()
    if result["mode"] != "PENDING" and not result["basis"]:
        raise ValidationError({"temperature_plan": "请填写测温时机的确认依据/负责人，不自动认定每包必测或不测。"})
    if result["mode"] == "REFERENCE" and not result["reference"]:
        raise ValidationError({"temperature_plan": "不重复测温时请明确对应的实际测温记录或原卡号。"})
    return result
