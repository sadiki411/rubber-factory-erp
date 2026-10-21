"""Human-confirmed, locally recognized Aslin purchase orders.

Recognition only archives a draft. The ordinary business import transaction is
the sole writer of orders/products; images never create process cards or stock.
"""

import hashlib
import io
import re
from datetime import datetime
from decimal import Decimal
from pathlib import PurePath
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from django.core.files.base import ContentFile
from django.db import transaction
from PIL import Image, ImageOps
from rest_framework import serializers

from molds.models import MoldModel

from .imports import (
    RECORD_ORDER, RECORD_PRODUCT, _external_key, _issue, _prepare_record_actions,
    _record_base, _validate_duplicate_business_keys, business_preview_result,
)
from .models import BusinessImportBatch, normalize_product_key
from .services import json_safe


CUSTOMER = "艾斯霖"
PARSER = "aslin-purchase-images-v1"
MAX_FILES = 10
MAX_TOTAL_BYTES = 20 * 1024 * 1024
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 25_000_000
DATE_RE = r"20\d{2}[/.-]\d{1,2}[/.-]\d{1,2}"


class PurchaseRowSerializer(serializers.Serializer):
    item_no = serializers.CharField(max_length=100)
    product_name = serializers.CharField(max_length=200, allow_blank=True, default="")
    specification = serializers.CharField(max_length=200)
    material = serializers.CharField(max_length=100)
    order_quantity = serializers.IntegerField(min_value=1, max_value=2147483647)
    due_date = serializers.DateField()
    product_specification_id = serializers.IntegerField(min_value=1, allow_null=True, required=False)
    unit_price = serializers.DecimalField(
        max_digits=18, decimal_places=4, min_value=0, allow_null=True, required=False,
    )
    amount = serializers.DecimalField(
        max_digits=18, decimal_places=4, min_value=0, allow_null=True, required=False,
    )


class PurchaseDocumentSerializer(serializers.Serializer):
    document_id = serializers.UUIDField()
    order_no = serializers.RegexField(r"^CG\d{12}$", max_length=100)
    order_date = serializers.DateField()
    rows = PurchaseRowSerializer(many=True, allow_empty=False, max_length=100)


def _read_image(upload):
    upload.seek(0)
    raw = upload.read(MAX_FILE_BYTES + 1)
    upload.seek(0)
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError("单张采购单图片不能超过10MB。")
    try:
        with Image.open(io.BytesIO(raw)) as check:
            if check.format not in {"PNG", "JPEG"} or getattr(check, "n_frames", 1) != 1:
                raise ValueError("仅支持单张PNG、JPG或JPEG采购单图片。")
            if check.width * check.height > MAX_PIXELS:
                raise ValueError("采购单图片像素不能超过2500万，请压缩后重试。")
            check.verify()
        with Image.open(io.BytesIO(raw)) as source:
            image = ImageOps.exif_transpose(source).convert("L")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("无法读取采购单图片，请使用有效的PNG或JPG图片。") from exc
    return raw, ImageOps.autocontrast(image)


def _bands(values):
    groups = []
    for value in values:
        if groups and value <= groups[-1][-1] + 1:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [round(sum(group) / len(group)) for group in groups]


def _table_grid(image):
    """Find the printed ruling, rather than rely on screenshot coordinates."""
    width, height = image.size
    pixels = image.load()
    left, right = int(width * .03), int(width * .97)
    horizontal = _bands([
        y for y in range(int(height * .10), int(height * .60))
        if sum(pixels[x, y] < 100 for x in range(left, right)) > (right - left) * .78
    ])
    # Supplier/contact row, table header, then the actual purchase lines.
    if len(horizontal) < 5:
        return None
    top, bottom = horizontal[1], horizontal[2]
    vertical = _bands([
        x for x in range(left, right)
        if sum(pixels[x, y] < 100 for y in range(top + 2, bottom - 1))
        > max(1, bottom - top - 3) * .90
    ])
    if len(vertical) != 9:
        return None
    return horizontal, vertical


def _date_text(value):
    if not value:
        return ""
    try:
        return datetime.strptime(re.sub(r"[.-]", "/", value), "%Y/%m/%d").date().isoformat()
    except ValueError:
        return ""


def _number_text(text, *, integer=False):
    value = re.sub(r"\s", "", text).replace("，", ",")
    # A comma may be a misread decimal point (30000,0). Only conventional
    # thousands grouping may be removed; an ambiguous count stays blank.
    if not re.fullmatch(r"(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?", value):
        return None
    number = Decimal(value.replace(",", ""))
    if integer:
        return int(number) if number == number.to_integral_value() and number > 0 else None
    return str(number)


def split_product_identity(text):
    compact = re.sub(r"\s+", "", text).upper().translate(str.maketrans({
        "【": "[", "】": "]", "（": "[", "）": "]", "(": "[", ")": "]",
        "—": "-", "–": "-",
    }))
    match = re.fullmatch(r"([A-Z]+\d+-\d+[A-Z0-9._-]*)\[([A-Z0-9][A-Z0-9._-]*)\]", compact)
    return (match.group(1), match.group(2)) if match else ("", "")


def recognize_purchase_image(image):
    import pytesseract

    def ocr(crop, *, numeric=False, code=False, chinese=False, psm=6):
        # Upscaling is for recognition only; archived originals are unchanged.
        crop = ImageOps.expand(crop.resize((crop.width * 3, crop.height * 3)), 12, fill=255)
        config = f"--psm {psm}"
        if numeric:
            config += " -c tessedit_char_whitelist=0123456789.,/-"
        elif code:
            config += " -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789[]().,/:-"
        return pytesseract.image_to_string(
            crop, lang="chi_sim" if chinese else "eng" if numeric or code else "chi_sim+eng", config=config, timeout=8,
        ).strip()

    warnings = ["图片识别可能误读字符，请逐项核对采购单号、规格、材质、数量和交期后再预检。"]
    try:
        header = ocr(image.crop((0, 0, image.width, int(image.height * .18))))
        order_numbers = set(re.findall(r"CG\s*\d{12}", header, flags=re.I))
        order_no = re.sub(r"\s+", "", next(iter(order_numbers))).upper() if len(order_numbers) == 1 else ""
        date_match = re.search(DATE_RE, header)
        order_date = _date_text(date_match.group()) if date_match else ""
        grid = _table_grid(image)
        rows, texts = [], [header]
        if grid:
            horizontal, vertical = grid
            metadata = ocr(image.crop((
                vertical[5] + int((vertical[6] - vertical[5]) * .86),
                max(0, horizontal[0] - int(image.height * .085)),
                vertical[-1], horizontal[0] - 2,
            )), code=True)
            texts.append(metadata)
            numbers = set(re.findall(r"CG\d{12}", metadata, flags=re.I))
            if len(numbers) == 1:
                order_no = next(iter(numbers)).upper()
            date_match = re.search(DATE_RE, metadata)
            if date_match:
                order_date = _date_text(date_match.group())
            for top, bottom in zip(horizontal[2:], horizontal[3:]):
                def cell(index, **options):
                    crop = image.crop((vertical[index] + 2, top + 2, vertical[index + 1] - 1, bottom - 1))
                    if sum(crop.histogram()[:180]) < 5:
                        return ""
                    if options.get("code"):
                        # Wrapped materials belong to the same table cell.
                        # Recognize each printed text line independently so a
                        # lone closing bracket does not distort the first line.
                        ink_rows = [y for y in range(crop.height) if sum(
                            crop.getpixel((x, y)) < 160 for x in range(crop.width)
                        ) > 1]
                        groups = []
                        for y in ink_rows:
                            if groups and y <= groups[-1][-1] + 1:
                                groups[-1].append(y)
                            else:
                                groups.append([y])
                        return "".join(ocr(crop.crop((0, group[0], crop.width, group[-1] + 1)), psm=7, **options) for group in groups)
                    return ocr(crop, **options)

                identity = cell(1, code=True)
                texts.append(identity)
                if "以下" in identity or "空白" in identity:
                    break
                if not re.search(r"\d", identity):
                    continue
                specification, material = split_product_identity(identity)
                if not specification:
                    warnings.append(f"第{len(rows) + 1}项规格/材质未能可靠拆分，原文：{identity}。请手动核对。")
                name, quantity = cell(0, chinese=True), cell(3, numeric=True)
                price, amount = cell(4, numeric=True), cell(5, numeric=True)
                due = cell(6, numeric=True)
                texts.extend([name, quantity, price, amount, due])
                rows.append({
                    "item_no": str(len(rows) + 1), "product_name": name,
                    "specification": specification, "material": material,
                    "order_quantity": _number_text(quantity, integer=True),
                    "unit_price": _number_text(price), "amount": _number_text(amount),
                    "due_date": _date_text(due),
                })
        if not rows:
            warnings.append("未识别出可靠的明细表格，请对照原图手动补齐；系统未猜填业务数据。")
            rows = [{"item_no": "1", "product_name": "", "specification": "", "material": "", "order_quantity": None, "due_date": "", "unit_price": None, "amount": None}]
        return {"order_no": order_no, "order_date": order_date, "rows": rows, "raw_text": "\n".join(texts), "warnings": warnings}
    except (pytesseract.TesseractNotFoundError, pytesseract.TesseractError, RuntimeError) as exc:
        raise ValueError("本地图片识别暂不可用或超时，请稍后重试；没有写入任何订单。") from exc


def recognize_purchase_images(files, user):
    if not files or len(files) > MAX_FILES:
        raise ValueError("请一次选择1至10张艾斯霖采购单图片。")
    # Validate the complete request before starting costly OCR or writing files.
    images, total = [], 0
    for upload in files:
        raw, image = _read_image(upload)
        total += len(raw)
        if total > MAX_TOTAL_BYTES:
            raise ValueError("采购单图片总大小不能超过20MB。")
        images.append((upload, raw, image))
    documents, archive_buffer = [], io.BytesIO()
    with ZipFile(archive_buffer, "w", ZIP_DEFLATED) as archive:
        for index, (upload, raw, image) in enumerate(images, start=1):
            file_name = PurePath(str(upload.name).replace("\\", "/")).name[:150]
            extension = ".png" if image.format == "PNG" or raw[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg"
            archive_name = f"{index:02d}{extension}"
            archive.writestr(archive_name, raw)
            documents.append({
                **recognize_purchase_image(image), "document_id": str(uuid4()),
                "file_name": file_name, "archive_name": archive_name,
                "sha256": hashlib.sha256(raw).hexdigest(),
            })
    data = archive_buffer.getvalue()
    batch = BusinessImportBatch(
        source_type=BusinessImportBatch.SourceType.PURCHASE_ORDER_IMAGES, parser=PARSER,
        original_name=f"艾斯霖采购单-{len(documents)}张.zip", sha256=hashlib.sha256(data).hexdigest(),
        payload={"parser": PARSER, "documents": documents, "rows": [], "human_confirmed": False},
        created_by=user,
    )
    batch.original_file.save(batch.original_name, ContentFile(data), save=False)
    batch.save()
    return {"token": str(batch.pk), "customer": CUSTOMER, "documents": documents, "requires_human_confirmation": True}


def _purchase_records(documents, originals, sha256):
    records, issues, products = [], [], {}
    mold_models = {model.code.casefold(): model for model in MoldModel.objects.filter(
        code__in={row["specification"] for doc in documents for row in doc["rows"]},
    )}
    for document_index, document in enumerate(documents, start=1):
        original = originals[str(document["document_id"])]
        sheet = f"采购单图片{document_index}"
        for row_index, row in enumerate(document["rows"], start=1):
            raw_data = {
                "customer": CUSTOMER, "file_name": original["file_name"],
                "archive_name": original["archive_name"], "image_sha256": original["sha256"],
                "ocr_original": original, "confirmed_document": json_safe(document),
                "confirmed_row": json_safe(row), "unit": "PCS",
                "item_no_origin": "ERP明细序号（原采购单未提供项次）",
            }
            key = (row["specification"].casefold(), row["material"].casefold(), row.get("product_specification_id"))
            product = products.get(key)
            if product is None:
                product = _record_base(sha256, sheet, row_index, RECORD_PRODUCT, raw_data)
                model = mold_models.get(row["specification"].casefold())
                product.update({
                    "product_name": row["product_name"], "customer_product_no": row["specification"],
                    "specification": row["specification"], "material": row["material"],
                    "mold_no": model.code if model else "", "source_system": CUSTOMER,
                    "selected_product_specification_id": row.get("product_specification_id"),
                    "source_fields": ["product_name", "customer_product_no", "specification", "material"],
                })
                if model:
                    product["mold_model_id"] = model.pk
                    product["source_fields"].extend(["mold_no", "mold_model_id"])
                product["normalized_key"] = normalize_product_key(
                    row["product_name"], row["specification"], row["specification"], row["material"], product["mold_no"],
                )
                products[key] = product
                records.append(product)
                if not model:
                    issues.append(_issue("warning", "未精确匹配到同编号模具，暂不自动绑定或新建模具，可在产品资料中手动关联。", sheet=sheet, row=row_index, field="mold_model"))
            order = _record_base(sha256, sheet, row_index, RECORD_ORDER, raw_data)
            order.update({
                "order_no": document["order_no"], "item_no": row["item_no"],
                "product_name": row["product_name"], "product_code": row["specification"],
                "specification": row["specification"], "material": row["material"],
                "order_quantity": row["order_quantity"], "order_date": document["order_date"].isoformat(),
                "due_date": row["due_date"].isoformat(), "production_required": True,
                "source_system": CUSTOMER, "external_key": _external_key("purchase-order", CUSTOMER, document["order_no"], row["item_no"]),
                "source_document_at": datetime.combine(document["order_date"], datetime.min.time()).isoformat(),
                "product_spec_source_key": product["source_key"],
                "source_fields": ["order_no", "item_no", "product_name", "product_code", "specification", "material", "order_quantity", "order_date", "due_date"],
            })
            records.append(order)
            if row.get("unit_price") is not None and row.get("amount") is not None:
                expected = row["unit_price"] * row["order_quantity"]
                if abs(expected - row["amount"]) > Decimal("0.01"):
                    issues.append(_issue("error", "数量×单价与金额不一致，请对照原图核对。", sheet=sheet, row=row_index, field="amount"))
    _validate_duplicate_business_keys(records, issues)
    return records, issues


def preview_purchase_images(batch, documents, user, *, confirmed=False):
    if confirmed is not True:
        raise ValueError("请先确认已核对所有采购单图片识别结果。")
    serializer = PurchaseDocumentSerializer(data=documents, many=True, allow_empty=False, max_length=MAX_FILES)
    serializer.is_valid(raise_exception=True)
    validated = serializer.validated_data
    with transaction.atomic():
        current = BusinessImportBatch.objects.select_for_update().get(pk=batch.pk, created_by=user)
        if current.source_type != BusinessImportBatch.SourceType.PURCHASE_ORDER_IMAGES or current.status != BusinessImportBatch.Status.PREVIEWED:
            raise ValueError("该图片批次已经提交或不属于采购单图片，请重新识别。")
        originals = {doc["document_id"]: doc for doc in current.payload.get("documents", [])}
        identities = [str(doc["document_id"]) for doc in validated]
        if len(set(identities)) != len(identities) or set(identities) != set(originals):
            raise ValueError("采购单图片与识别批次不一致，请重新识别，不允许遗漏或替换原图。")
        records, issues = _purchase_records(validated, originals, current.sha256)
        _prepare_record_actions(records, current.source_type, issues)
        current.payload.update({"rows": json_safe(records), "confirmed_documents": json_safe(validated), "human_confirmed": True})
        current.errors = [issue for issue in issues if issue["level"] == "error"]
        current.warnings = [issue for issue in issues if issue["level"] == "warning"]
        current.save(update_fields=["payload", "errors", "warnings"])
        return business_preview_result(current, records, issues)
