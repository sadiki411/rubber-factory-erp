"""Conservative local OCR preview for photographed production ledger sheets.

The result is always a draft.  It never writes to business tables and never
fills a missing critical number by guessing.  One photo maps to one task and
the lower handwritten table maps to zero or more cumulative counter rows.
"""

import io
import re

from PIL import Image, ImageOps


MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 25_000_000
CRITICAL_TASK_FIELDS = ("order_no", "cavities", "planned_mold_count")
LABELS = {
    "order_no": ("订单号", "订单编号", "采购订单"),
    "specification": ("规格",),
    "material": ("材质",),
    "order_quantity": ("订单数量",),
    "cavities": ("模具孔数", "孔数"),
    "compound_size": ("胶料尺寸",),
    "estimated_defect": ("预估不良",),
    "curing_seconds": ("硫化时间",),
    "planned_mold_count": ("订单模数",),
}
INTEGER_FIELDS = {
    "order_quantity",
    "cavities",
    "curing_seconds",
    "planned_mold_count",
}
LOG_HEADER_LABELS = ("生产日期", "作业员", "生产模数")
DATE_PATTERN = re.compile(
    r"(?P<date>20\d{2}[年./-]\d{1,2}(?:[月./-]\d{1,2}日?)?)"
)


def _safe_image(uploaded_file):
    size = getattr(uploaded_file, "size", None)
    if size is not None and size > MAX_IMAGE_BYTES:
        raise ValueError("单张照片不能超过10MB。")
    uploaded_file.seek(0)
    raw = uploaded_file.read(MAX_IMAGE_BYTES + 1)
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("单张照片不能超过10MB。")
    try:
        image = Image.open(io.BytesIO(raw))
        image.verify()
        image = Image.open(io.BytesIO(raw)).convert("L")
    except Exception as exc:
        raise ValueError("无法读取照片，请上传JPG或PNG图片。") from exc
    if image.width * image.height > MAX_PIXELS:
        raise ValueError("照片像素过大，请压缩后重试。")
    return ImageOps.autocontrast(image)


def _normalize_date(raw):
    if not raw:
        return None
    normalized = re.sub(r"[年月./]", "-", raw).replace("日", "").strip("-")
    pieces = normalized.split("-")
    if len(pieces) != 3:
        return None
    try:
        return f"{int(pieces[0]):04d}-{int(pieces[1]):02d}-{int(pieces[2]):02d}"
    except ValueError:
        return None


def _extract_task(lines):
    draft = {}
    confidences = {}
    for index, line in enumerate(lines):
        text = line["text"].strip()
        compact = text.replace(" ", "")
        if not compact:
            continue
        for field, labels in LABELS.items():
            label = next((item for item in labels if item in compact), None)
            if label is None:
                continue
            tail = compact.split(label, 1)[1].lstrip("：:")
            if not tail and index + 1 < len(lines):
                tail = lines[index + 1]["text"].strip()
            if field == "order_no":
                match = re.search(r"[A-Za-z0-9][A-Za-z0-9_./-]{4,}", tail)
                value = match.group() if match else None
            elif field in INTEGER_FIELDS:
                match = re.search(r"\d+", tail.replace(",", ""))
                value = int(match.group()) if match else None
            else:
                value = tail or None
            if value not in (None, ""):
                draft[field] = value
                confidences[field] = float(line.get("confidence") or 0)
    return draft, confidences


def _extract_log_rows(lines):
    header_index = None
    for index, line in enumerate(lines):
        compact = line["text"].replace(" ", "")
        if any(label in compact for label in LOG_HEADER_LABELS):
            header_index = index
            break
    if header_index is None:
        return [], [
            {"field": "logs", "message": "未识别到交接读数表头，需人工录入或重新拍照。"}
        ]

    result = []
    blockers = []
    for source_row, line in enumerate(lines[header_index + 1 :], 1):
        text = line["text"].strip()
        if not text or any(label in text.replace(" ", "") for label in LOG_HEADER_LABELS):
            continue
        date_match = DATE_PATTERN.search(text)
        date_value = _normalize_date(date_match.group("date")) if date_match else None
        without_date = DATE_PATTERN.sub(" ", text, count=1)
        # The paper order is date | operator | cumulative molds | remaining
        # molds, so choose the first integer after removing a date, not the last.
        count_match = re.search(r"(?<![A-Za-z])\d+(?![A-Za-z])", without_date.replace(",", ""))
        if not count_match:
            continue
        cumulative = int(count_match.group())
        if cumulative < 1:
            continue
        prefix = without_date[: count_match.start()]
        shift = "DAY" if "白班" in prefix else "NIGHT" if "夜班" in prefix else ""
        operator = re.sub(r"白班|夜班|[-:：/年月日.]", " ", prefix)
        operator = " ".join(operator.split())
        confidence = float(line.get("confidence") or 0)
        blocking = []
        if confidence < 85:
            issue = {
                "field": "cumulative_mold_count",
                "source_row": source_row,
                "message": (
                    f"交接第{source_row}行累计模数识别置信度"
                    f"{confidence:.1f}%，必须人工核对。"
                ),
            }
            blocking.append(issue)
            blockers.append(issue)
        result.append(
            {
                "source_row": source_row,
                "production_date": date_value,
                "shift": shift,
                "operator": operator,
                "cumulative_mold_count": cumulative,
                "confidence": confidence,
                "field_confidence": {
                    "date": confidence if date_value else 0,
                    "shift": confidence if shift else 0,
                    "operator": confidence if operator else 0,
                    "cumulative_mold_count": confidence,
                },
                "blocking_items": blocking,
                "requires_human_confirmation": True,
            }
        )
    if not result:
        blockers.append(
            {"field": "logs", "message": "未识别到累计模数行，必须人工补录。"}
        )
    return result, blockers


def _group_lines(data):
    grouped = {}
    for index, raw_text in enumerate(data.get("text", [])):
        text = str(raw_text or "").strip()
        if not text:
            continue
        try:
            confidence = float(data["conf"][index])
        except (ValueError, TypeError, IndexError):
            confidence = -1
        key = (
            data.get("block_num", [0] * (index + 1))[index],
            data.get("par_num", [0] * (index + 1))[index],
            data.get("line_num", list(range(index + 1)))[index],
        )
        bucket = grouped.setdefault(key, {"words": [], "confidence": []})
        bucket["words"].append(text)
        if confidence >= 0:
            bucket["confidence"].append(confidence)
    return [
        {
            "text": " ".join(item["words"]),
            "confidence": (
                round(sum(item["confidence"]) / len(item["confidence"]), 1)
                if item["confidence"]
                else 0
            ),
        }
        for item in grouped.values()
    ]


def _critical_task_blockers(draft, confidences):
    blocking = []
    for field in CRITICAL_TASK_FIELDS:
        value = draft.get(field)
        confidence = confidences.get(field, 0)
        if value in (None, ""):
            blocking.append(
                {"field": field, "message": "关键字段未识别，必须人工填写确认。"}
            )
        elif confidence < 85:
            blocking.append(
                {
                    "field": field,
                    "message": f"识别置信度{confidence:.1f}%，必须人工核对确认。",
                }
            )
    return blocking


def _orient_image(image, pytesseract, output_type):
    rotation = 0
    try:
        osd = pytesseract.image_to_osd(image, output_type=output_type.DICT)
        rotation = int(osd.get("rotate") or 0) % 360
    except Exception:
        # Orientation detection can fail on a mostly empty handwritten sheet.
        # Returning 0 is explicit and the preview remains human-confirmed.
        rotation = 0
    if rotation:
        image = image.rotate(rotation, expand=True)
    return image, rotation


def preview_production_photos(files):
    try:
        import pytesseract
        from pytesseract import Output
    except ImportError as exc:
        raise RuntimeError(
            "服务器尚未安装本地OCR组件；本次没有保存数据。请安装pytesseract和Tesseract中文语言包。"
        ) from exc

    tasks = []
    for uploaded in files:
        image = _safe_image(uploaded)
        image, rotation = _orient_image(image, pytesseract, Output)
        try:
            data = pytesseract.image_to_data(
                image,
                lang="chi_sim+eng",
                config="--psm 6",
                output_type=Output.DICT,
            )
        except Exception as exc:
            raise RuntimeError(
                "服务器本地OCR不可用或缺少chi_sim中文语言包；本次没有保存任何数据。"
            ) from exc
        lines = _group_lines(data)
        draft, confidences = _extract_task(lines)
        logs, log_blockers = _extract_log_rows(lines)
        draft["logs"] = logs
        blocking = _critical_task_blockers(draft, confidences)
        blocking.extend(log_blockers)
        tasks.append(
            {
                "file_name": getattr(uploaded, "name", "photo"),
                "rotation_degrees": rotation,
                "draft": draft,
                "field_confidence": confidences,
                "text_lines": lines,
                "blocking_items": blocking,
                "requires_human_confirmation": True,
            }
        )
    return {
        "available": True,
        "tasks": tasks,
        "can_commit": False,
        "requires_human_confirmation": True,
    }
