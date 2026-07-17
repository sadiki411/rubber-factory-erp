import hashlib
import io
import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import PurePosixPath
from posixpath import dirname, join, normpath
from uuid import UUID
from zipfile import BadZipFile, ZipFile
from xml.etree import ElementTree

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.files.base import ContentFile
from django.db import IntegrityError, OperationalError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.styles.numbers import BUILTIN_FORMATS, is_date_format
from openpyxl.utils import get_column_letter
from openpyxl.utils.datetime import CALENDAR_MAC_1904, CALENDAR_WINDOWS_1900, from_excel

from quality.models import QualityOrder

from .models import (
    BusinessImportBatch,
    BusinessRecordRevision,
    MaterialReceipt,
    ProductInspectionCriterion,
    ProductSpecification,
    normalize_product_key,
)
from .services import json_safe, record_revision


MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_FILES = 1000
MAX_WORKSHEETS = 100
MAX_ROWS_PER_SHEET = 5000
MAX_COLUMNS_PER_SHEET = 100
MAX_WORKBOOK_CELLS = 300_000

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
NS = {"m": MAIN_NS, "r": REL_NS, "pr": PACKAGE_REL_NS}

RECORD_PRODUCT = "PRODUCT_SPECIFICATION"
RECORD_ORDER = "ORDER"
RECORD_RECEIPT = "MATERIAL_RECEIPT"
RECORD_CRITERION = "INSPECTION_CRITERION"
RECORD_TYPES = (RECORD_PRODUCT, RECORD_ORDER, RECORD_RECEIPT, RECORD_CRITERION)


@dataclass
class CellData:
    raw_value: object = None
    display_text: str = ""
    number_format: str = "General"

    def raw_payload(self):
        return {
            "raw_value": _json_cell_value(self.raw_value),
            "display_text": self.display_text,
            "number_format": self.number_format,
        }


@dataclass
class SheetData:
    name: str
    rows: list


def _json_cell_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _issue(level, message, *, sheet="", row=None, field=""):
    return {
        "level": level,
        "sheet": sheet,
        "row": row,
        "field": field,
        "message": message,
    }


def _clean_text(value):
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _literal_number_format(number_format):
    text = str(number_format or "").split(";", 1)[0].strip()
    if not text or text.lower() == "general" or is_date_format(text):
        return ""
    if re.search(r"[0#?]", text):
        return ""
    text = re.sub(r"\[[^\]]+\]", "", text)
    text = re.sub(r"_.", "", text)
    text = re.sub(r"\*.", "", text)
    text = re.sub(r'"([^"]*)"', r"\1", text)
    text = re.sub(r"\\(.)", r"\1", text)
    return text.strip()


def _display_value(value, number_format="General", *, date_cell=False):
    if value is None:
        return ""
    if isinstance(value, datetime):
        if value.time().replace(microsecond=0) == datetime.min.time():
            return value.date().isoformat()
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    literal = _literal_number_format(number_format)
    if literal and isinstance(value, (int, float, Decimal)) and not date_cell:
        return literal
    return _clean_text(value)


def _validate_archive(data):
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(f"Excel文件不能超过{MAX_UPLOAD_BYTES // 1024 // 1024}MB。")
    try:
        with ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if len(members) > MAX_ARCHIVE_FILES:
                raise ValueError("Excel压缩包包含的文件数量过多。")
            if sum(item.file_size for item in members) > MAX_UNCOMPRESSED_BYTES:
                raise ValueError("Excel解压后的数据量过大。")
            for item in members:
                path = PurePosixPath(item.filename)
                if path.is_absolute() or ".." in path.parts:
                    raise ValueError("Excel包含不安全的压缩包路径。")
    except BadZipFile as exc:
        raise ValueError("文件不是有效的.xlsx工作簿。") from exc


def _validate_dimensions(sheets):
    if len(sheets) > MAX_WORKSHEETS:
        raise ValueError(f"工作表不能超过{MAX_WORKSHEETS}个。")
    total = 0
    for sheet in sheets:
        if len(sheet.rows) > MAX_ROWS_PER_SHEET:
            raise ValueError(f"工作表“{sheet.name}”不能超过{MAX_ROWS_PER_SHEET}行。")
        max_columns = max((len(cells) for _, cells in sheet.rows), default=0)
        if max_columns > MAX_COLUMNS_PER_SHEET:
            raise ValueError(f"工作表“{sheet.name}”不能超过{MAX_COLUMNS_PER_SHEET}列。")
        total += len(sheet.rows) * max_columns
        if total > MAX_WORKBOOK_CELLS:
            raise ValueError("工作簿单元格数量过多。")


def _openpyxl_sheets(data):
    workbook = load_workbook(io.BytesIO(data), read_only=False, data_only=False)
    try:
        sheets = []
        for worksheet in workbook.worksheets:
            rows = []
            for row_no, row in enumerate(worksheet.iter_rows(), start=1):
                cells = []
                last_nonempty = -1
                for index, cell in enumerate(row):
                    value = getattr(cell, "value", None)
                    date_cell = bool(getattr(cell, "is_date", False))
                    number_format = getattr(cell, "number_format", "General") or "General"
                    cells.append(
                        CellData(
                            raw_value=value,
                            display_text=_display_value(
                                value, number_format, date_cell=date_cell
                            ),
                            number_format=number_format,
                        )
                    )
                    if value not in (None, "") or cells[-1].display_text:
                        last_nonempty = index
                if last_nonempty >= 0:
                    rows.append((row_no, cells[: last_nonempty + 1]))
            sheets.append(SheetData(worksheet.title, rows))
        return sheets
    finally:
        workbook.close()


def _relationship_target(base_path, target):
    target = target.replace("\\", "/")
    if target.startswith("/"):
        return target.lstrip("/")
    return normpath(join(dirname(base_path), target))


def _parse_styles(archive):
    formats = dict(BUILTIN_FORMATS)
    style_formats = []
    if "xl/styles.xml" not in archive.namelist():
        return formats, style_formats
    root = ElementTree.fromstring(archive.read("xl/styles.xml"))
    num_fmts = root.find("m:numFmts", NS)
    if num_fmts is not None:
        for item in num_fmts:
            try:
                formats[int(item.attrib["numFmtId"])] = item.attrib.get("formatCode", "General")
            except (KeyError, ValueError):
                continue
    cell_xfs = root.find("m:cellXfs", NS)
    if cell_xfs is not None:
        for xf in cell_xfs:
            try:
                num_fmt_id = int(xf.attrib.get("numFmtId", "0"))
            except ValueError:
                num_fmt_id = 0
            style_formats.append(formats.get(num_fmt_id, "General"))
    return formats, style_formats


def _xml_cell_value(cell, shared_strings, style_formats, epoch):
    cell_type = cell.attrib.get("t", "n")
    style_id = int(cell.attrib.get("s", "0") or 0)
    number_format = (
        style_formats[style_id] if 0 <= style_id < len(style_formats) else "General"
    )
    value_node = cell.find("m:v", NS)
    formula_node = cell.find("m:f", NS)
    raw_text = value_node.text if value_node is not None else None
    raw_value = None
    if cell_type == "s" and raw_text not in (None, ""):
        raw_value = shared_strings[int(raw_text)]
    elif cell_type == "inlineStr":
        inline = cell.find("m:is", NS)
        raw_value = (
            "".join(item.text or "" for item in inline.iter(f"{{{MAIN_NS}}}t"))
            if inline is not None
            else ""
        )
    elif cell_type in {"str", "e"}:
        raw_value = raw_text or ""
    elif cell_type == "b":
        raw_value = raw_text == "1"
    elif cell_type == "d":
        raw_value = parse_datetime(raw_text or "") or parse_date(raw_text or "") or raw_text
    elif raw_text not in (None, ""):
        try:
            numeric = float(raw_text)
            raw_value = int(numeric) if numeric.is_integer() else numeric
        except ValueError:
            raw_value = raw_text
    elif formula_node is not None:
        raw_value = f"={formula_node.text or ''}"

    date_cell = bool(isinstance(raw_value, (int, float)) and is_date_format(number_format))
    display_value = raw_value
    if date_cell:
        try:
            display_value = from_excel(raw_value, epoch=epoch)
        except (ValueError, TypeError, OverflowError):
            display_value = raw_value
            date_cell = False
    return CellData(
        raw_value=raw_value,
        display_text=_display_value(display_value, number_format, date_cell=date_cell),
        number_format=number_format,
    )


def _ooxml_sheets(data):
    with ZipFile(io.BytesIO(data)) as archive:
        shared_strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared_root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in shared_root.findall("m:si", NS):
                shared_strings.append(
                    "".join(node.text or "" for node in item.iter(f"{{{MAIN_NS}}}t"))
                )
        _, style_formats = _parse_styles(archive)
        workbook_path = "xl/workbook.xml"
        workbook_root = ElementTree.fromstring(archive.read(workbook_path))
        properties = workbook_root.find("m:workbookPr", NS)
        date_1904 = properties is not None and properties.attrib.get("date1904") in {
            "1",
            "true",
            "True",
        }
        epoch = CALENDAR_MAC_1904 if date_1904 else CALENDAR_WINDOWS_1900
        relationship_path = "xl/_rels/workbook.xml.rels"
        relationships = ElementTree.fromstring(archive.read(relationship_path))
        targets = {
            item.attrib["Id"]: _relationship_target(workbook_path, item.attrib["Target"])
            for item in relationships
        }
        sheets = []
        for sheet in workbook_root.find("m:sheets", NS):
            name = sheet.attrib.get("name", "Sheet")
            target = targets[sheet.attrib[f"{{{REL_NS}}}id"]]
            root = ElementTree.fromstring(archive.read(target))
            rows = []
            for row in root.findall(".//m:sheetData/m:row", NS):
                row_no = int(row.attrib.get("r", "0") or 0)
                cells_by_index = {}
                max_index = -1
                for cell in row.findall("m:c", NS):
                    reference = cell.attrib.get("r", "A1")
                    letters = re.match(r"[A-Z]+", reference)
                    if not letters:
                        continue
                    index = 0
                    for letter in letters.group(0):
                        index = index * 26 + ord(letter) - 64
                    index -= 1
                    cells_by_index[index] = _xml_cell_value(
                        cell, shared_strings, style_formats, epoch
                    )
                    max_index = max(max_index, index)
                if max_index >= 0:
                    rows.append(
                        (
                            row_no,
                            [cells_by_index.get(index, CellData()) for index in range(max_index + 1)],
                        )
                    )
            sheets.append(SheetData(name, rows))
        return sheets


def read_business_workbook(data):
    _validate_archive(data)
    try:
        sheets = _openpyxl_sheets(data)
        parser = "openpyxl-3.1"
    except (TypeError, ValueError, KeyError, IndexError, AttributeError, OSError):
        sheets = _ooxml_sheets(data)
        parser = "safe-ooxml-1"
    _validate_dimensions(sheets)
    return sheets, parser


def _cell(cells, index):
    return cells[index] if 0 <= index < len(cells) else CellData()


def _normalized_header(value):
    return re.sub(r"[\s（）()：:]", "", str(value or "")).casefold()


def _header_map(cells):
    result = {}
    for index, cell in enumerate(cells):
        key = _normalized_header(cell.display_text)
        if key:
            result[key] = index
    return result


def _find_header(sheet, required, max_scan=20):
    normalized_required = {_normalized_header(item) for item in required}
    for row_no, cells in sheet.rows[:max_scan]:
        mapping = _header_map(cells)
        if normalized_required.issubset(mapping):
            return row_no, mapping
    return None, None


def _sheet_row_iter(sheet, header_row):
    for row_no, cells in sheet.rows:
        if row_no > header_row:
            yield row_no, cells


def _mapped_cell(cells, mapping, *names):
    for name in names:
        index = mapping.get(_normalized_header(name))
        if index is not None:
            return _cell(cells, index)
    return CellData()


def _raw_row(cells, mapping):
    result = {}
    for header, index in mapping.items():
        result[header] = _cell(cells, index).raw_payload()
    return result


def _date_value(cell):
    value = cell.raw_value
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = cell.display_text.strip()
    parsed = parse_date(text)
    if parsed:
        return parsed.isoformat()
    for fmt in ("%Y/%m/%d", "%Y.%m.%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _decimal_text(cell, issues, *, sheet, row, field, required=False):
    text = cell.display_text.strip()
    if not text:
        if required:
            issues.append(_issue("error", f"{field}不能为空。", sheet=sheet, row=row, field=field))
        return None
    try:
        value = Decimal(text.replace(",", ""))
    except InvalidOperation:
        issues.append(_issue("error", f"{field}不是有效数字：{text}", sheet=sheet, row=row, field=field))
        return None
    if value < 0:
        issues.append(_issue("error", f"{field}不能小于0。", sheet=sheet, row=row, field=field))
        return None
    return format(value, "f")


def _positive_integer(cell, issues, *, sheet, row, field):
    text = cell.display_text.replace(",", "").strip()
    try:
        value = Decimal(text)
    except InvalidOperation:
        issues.append(_issue("error", f"{field}不是有效整数：{text}", sheet=sheet, row=row, field=field))
        return None
    if value != value.to_integral_value() or value < 1:
        issues.append(_issue("error", f"{field}必须是大于0的整数。", sheet=sheet, row=row, field=field))
        return None
    return int(value)


def _boolean_value(cell):
    text = cell.display_text.strip().casefold()
    if text in {"是", "yes", "y", "true", "1", "生产"}:
        return True
    if text in {"否", "no", "n", "false", "0", "不生产"}:
        return False
    return None


def _source_key(sha256, sheet, row, record_type):
    safe_sheet = re.sub(r"\s+", " ", sheet).strip()
    return f"{sha256}:{safe_sheet}:{row}:{record_type}"


def _record_base(sha256, sheet, row, record_type, raw_data):
    return {
        "row_key": f"{sheet}:{row}:{record_type}",
        "record_type": record_type,
        "sheet": sheet,
        "row": row,
        "source_key": _source_key(sha256, sheet, row, record_type),
        "raw_data": raw_data,
    }


def _parse_product_specifications(sheet, header_row, mapping, sha256, issues):
    records = []
    for row_no, cells in _sheet_row_iter(sheet, header_row):
        important = [
            _mapped_cell(cells, mapping, "规格"),
            _mapped_cell(cells, mapping, "材质"),
            _mapped_cell(cells, mapping, "料长"),
            _mapped_cell(cells, mapping, "切料重"),
            _mapped_cell(cells, mapping, "一次加硫条件"),
        ]
        if not any(cell.display_text for cell in important):
            continue
        record = _record_base(
            sha256, sheet.name, row_no, RECORD_PRODUCT, _raw_row(cells, mapping)
        )
        record.update(
            {
                "product_name": _mapped_cell(cells, mapping, "产品名称").display_text,
                "customer_product_no": _mapped_cell(
                    cells, mapping, "客户产品号", "产品编号"
                ).display_text,
                "specification": _mapped_cell(cells, mapping, "规格").display_text,
                "material": _mapped_cell(cells, mapping, "材质").display_text,
                "material_length": _mapped_cell(cells, mapping, "料长").display_text,
                "cut_weight": _mapped_cell(cells, mapping, "切料重").display_text,
                "strip_count": _mapped_cell(cells, mapping, "条数").display_text,
                "primary_curing": _mapped_cell(cells, mapping, "一次加硫条件").display_text,
                "secondary_curing": _mapped_cell(
                    cells, mapping, "二烤条件", "二次加硫条件"
                ).display_text,
                "total_cavities": _mapped_cell(cells, mapping, "总孔数").display_text,
                "effective_cavities": _mapped_cell(cells, mapping, "有效孔数").display_text,
                "mold_in_stock": _mapped_cell(cells, mapping, "模具在库").display_text,
                "mold_no": _mapped_cell(cells, mapping, "模具号").display_text,
                "mold_size": _mapped_cell(cells, mapping, "模具尺寸").display_text,
                "standard_hours": _mapped_cell(cells, mapping, "标准工时").display_text,
                "notes": _mapped_cell(cells, mapping, "备注").display_text,
            }
        )
        if not any(
            (record["product_name"], record["customer_product_no"], record["specification"])
        ):
            issues.append(
                _issue(
                    "error",
                    "产品名称、客户产品号和规格至少填写一项。",
                    sheet=sheet.name,
                    row=row_no,
                    field="specification",
                )
            )
        record["normalized_key"] = normalize_product_key(
            record["product_name"],
            record["customer_product_no"],
            record["specification"],
            record["material"],
            record["mold_no"],
        )
        records.append(record)
    return records


def _parse_internal_orders(sheets, sha256, issues):
    records = []
    for sheet in sheets:
        header_row, mapping = _find_header(
            sheet, ["订单编号", "规格", "胶料配方", "交期", "订单量"]
        )
        if not mapping:
            continue
        for row_no, cells in _sheet_row_iter(sheet, header_row):
            core_names = (
                "订单编号",
                "规格",
                "胶料配方",
                "交期",
                "订单量",
                "成型工时",
                "下单时间",
                "模具尺寸",
                "出货日期",
            )
            if not any(_mapped_cell(cells, mapping, name).display_text for name in core_names):
                continue
            order_no = _mapped_cell(cells, mapping, "订单编号").display_text
            specification = _mapped_cell(cells, mapping, "规格").display_text
            quantity = _positive_integer(
                _mapped_cell(cells, mapping, "订单量"),
                issues,
                sheet=sheet.name,
                row=row_no,
                field="order_quantity",
            )
            if not order_no:
                issues.append(_issue("error", "订单号不能为空。", sheet=sheet.name, row=row_no, field="order_no"))
            if not specification:
                issues.append(_issue("error", "规格不能为空。", sheet=sheet.name, row=row_no, field="specification"))
            due_cell = _mapped_cell(cells, mapping, "交期")
            order_date_cell = _mapped_cell(cells, mapping, "下单时间")
            record = _record_base(
                sha256, sheet.name, row_no, RECORD_ORDER, _raw_row(cells, mapping)
            )
            record.update(
                {
                    "order_no": order_no,
                    "item_no": _mapped_cell(cells, mapping, "项次").display_text,
                    "batch_no": "",
                    "product_code": "",
                    "product_name": _mapped_cell(cells, mapping, "产品名称").display_text,
                    "specification": specification,
                    "material": _mapped_cell(cells, mapping, "胶料配方", "材质").display_text,
                    "order_quantity": quantity,
                    "order_date": _date_value(order_date_cell),
                    "due_date": _date_value(due_cell),
                    "mold_size": _mapped_cell(cells, mapping, "模具尺寸").display_text,
                    "forming_hours": _decimal_text(
                        _mapped_cell(cells, mapping, "成型工时"),
                        issues,
                        sheet=sheet.name,
                        row=row_no,
                        field="forming_hours",
                    )
                    if _mapped_cell(cells, mapping, "成型工时").display_text
                    else None,
                    "production_required": _boolean_value(
                        _mapped_cell(cells, mapping, "是否生产")
                    ),
                    "legacy_shipment_text": _mapped_cell(
                        cells, mapping, "出货日期", "出货信息"
                    ).display_text,
                    "required_material_kg": _decimal_text(
                        _mapped_cell(cells, mapping, "所需胶料", "胶料用量KG"),
                        issues,
                        sheet=sheet.name,
                        row=row_no,
                        field="required_material_kg",
                    )
                    if _mapped_cell(cells, mapping, "所需胶料", "胶料用量KG").display_text
                    else None,
                    "status": QualityOrder.Status.OPEN,
                    "notes": "",
                }
            )
            records.append(record)
    return records


def _link_existing_product_specifications(records, issues):
    candidates = {}
    for product in ProductSpecification.objects.filter(is_active=True).only(
        "id", "specification", "material"
    ):
        key = (
            " ".join(str(product.specification or "").split()).casefold(),
            " ".join(str(product.material or "").split()).casefold(),
        )
        candidates.setdefault(key, []).append(product.pk)
    for record in records:
        if record.get("record_type") != RECORD_ORDER:
            continue
        key = (
            " ".join(str(record.get("specification") or "").split()).casefold(),
            " ".join(str(record.get("material") or "").split()).casefold(),
        )
        matches = candidates.get(key, [])
        if len(matches) == 1:
            record["product_spec_id"] = matches[0]
        elif len(matches) > 1:
            issues.append(
                _issue(
                    "warning",
                    "存在多条相同规格和材质的产品规格资料，订单暂不自动关联，请在线确认。",
                    sheet=record["sheet"],
                    row=record["row"],
                    field="product_specification",
                )
            )


def _metadata_date(sheet, label):
    normalized = _normalized_header(label)
    for _, cells in sheet.rows[:10]:
        for index, cell in enumerate(cells):
            if normalized in _normalized_header(cell.display_text):
                for candidate in cells[index + 1 :]:
                    if candidate.display_text:
                        return _date_value(candidate)
    return None


def _parse_factory_work_contact(sheets, sha256, issues):
    main_sheet = None
    main_header = None
    main_mapping = None
    criteria_sheet = None
    criteria_header = None
    criteria_mapping = None
    for sheet in sheets:
        header_row, mapping = _find_header(
            sheet, ["独立需求号", "项次", "材质", "规格", "订单量"]
        )
        if mapping and "检验项目" not in mapping:
            main_sheet, main_header, main_mapping = sheet, header_row, mapping
        header_row, mapping = _find_header(
            sheet, ["独立需求号", "项次", "项目号", "检验项目", "下限", "上限"]
        )
        if mapping:
            criteria_sheet, criteria_header, criteria_mapping = sheet, header_row, mapping
    if main_sheet is None:
        raise ValueError("生产工作联络单缺少订单工作表。")

    records = []
    product_keys = {}
    products_by_source_key = {}
    order_keys = {}
    order_date_value = _metadata_date(main_sheet, "发单时间")
    for row_no, cells in _sheet_row_iter(main_sheet, main_header):
        order_no = _mapped_cell(cells, main_mapping, "独立需求号").display_text
        specification = _mapped_cell(cells, main_mapping, "规格").display_text
        if not any((order_no, specification, _mapped_cell(cells, main_mapping, "订单量").display_text)):
            continue
        item_no = _mapped_cell(cells, main_mapping, "项次").display_text
        product_record = _record_base(
            sha256,
            main_sheet.name,
            row_no,
            RECORD_PRODUCT,
            _raw_row(cells, main_mapping),
        )
        product_record.update(
            {
                "product_name": "",
                "customer_product_no": "",
                "specification": specification,
                "material": _mapped_cell(cells, main_mapping, "材质").display_text,
                "material_length": _mapped_cell(cells, main_mapping, "料长").display_text,
                "cut_weight": _mapped_cell(cells, main_mapping, "切料重").display_text,
                "strip_count": "",
                "primary_curing": _mapped_cell(cells, main_mapping, "一次加硫条件").display_text,
                "secondary_curing": _mapped_cell(cells, main_mapping, "二次加硫条件").display_text,
                "total_cavities": "",
                "effective_cavities": "",
                "mold_in_stock": "",
                "mold_no": _mapped_cell(cells, main_mapping, "模具号").display_text,
                "mold_size": _mapped_cell(cells, main_mapping, "模具尺寸").display_text,
                "standard_hours": _mapped_cell(cells, main_mapping, "参考工时", "标准工时").display_text,
                "notes": "",
            }
        )
        product_record["normalized_key"] = normalize_product_key(
            "",
            "",
            specification,
            product_record["material"],
            product_record["mold_no"],
        )
        records.append(product_record)
        products_by_source_key[product_record["source_key"]] = product_record
        product_keys[(order_no.casefold(), item_no.casefold())] = product_record["source_key"]

        quantity = _positive_integer(
            _mapped_cell(cells, main_mapping, "订单量"),
            issues,
            sheet=main_sheet.name,
            row=row_no,
            field="order_quantity",
        )
        order_record = _record_base(
            sha256, main_sheet.name, row_no, RECORD_ORDER, _raw_row(cells, main_mapping)
        )
        order_record.update(
            {
                "order_no": order_no,
                "item_no": item_no,
                "batch_no": "",
                "product_code": "",
                "product_name": "",
                "specification": specification,
                "material": product_record["material"],
                "order_quantity": quantity,
                "order_date": order_date_value,
                "due_date": _date_value(_mapped_cell(cells, main_mapping, "完成日", "交期")),
                "mold_size": product_record["mold_size"],
                "forming_hours": _decimal_text(
                    _mapped_cell(cells, main_mapping, "参考工时", "标准工时"),
                    issues,
                    sheet=main_sheet.name,
                    row=row_no,
                    field="forming_hours",
                ),
                "production_required": True,
                "legacy_shipment_text": "",
                "required_material_kg": _decimal_text(
                    _mapped_cell(cells, main_mapping, "胶料用量KG", "胶料用量（KG）"),
                    issues,
                    sheet=main_sheet.name,
                    row=row_no,
                    field="required_material_kg",
                ),
                "status": QualityOrder.Status.OPEN,
                "notes": "",
                "product_spec_source_key": product_record["source_key"],
            }
        )
        if not order_no:
            issues.append(_issue("error", "订单号不能为空。", sheet=main_sheet.name, row=row_no, field="order_no"))
        records.append(order_record)
        order_keys[(order_no.casefold(), item_no.casefold())] = order_record["source_key"]

    if criteria_sheet and criteria_mapping:
        for row_no, cells in _sheet_row_iter(criteria_sheet, criteria_header):
            order_no = _mapped_cell(cells, criteria_mapping, "独立需求号").display_text
            item_no = _mapped_cell(cells, criteria_mapping, "项次").display_text
            inspection_item = _mapped_cell(cells, criteria_mapping, "检验项目").display_text
            if not any((order_no, item_no, inspection_item)):
                continue
            link_key = (order_no.casefold(), item_no.casefold())
            product_source_key = product_keys.get(link_key)
            if not product_source_key:
                issues.append(
                    _issue(
                        "error",
                        "检验标准找不到对应的订单产品规格。",
                        sheet=criteria_sheet.name,
                        row=row_no,
                        field="item_no",
                    )
                )
            project_no = _mapped_cell(cells, criteria_mapping, "项目号").display_text
            linked_product = products_by_source_key.get(product_source_key)
            if linked_product is not None and project_no and not linked_product["customer_product_no"]:
                linked_product["customer_product_no"] = project_no
                linked_product["normalized_key"] = normalize_product_key(
                    linked_product["product_name"],
                    linked_product["customer_product_no"],
                    linked_product["specification"],
                    linked_product["material"],
                    linked_product["mold_no"],
                )
            record = _record_base(
                sha256,
                criteria_sheet.name,
                row_no,
                RECORD_CRITERION,
                _raw_row(cells, criteria_mapping),
            )
            record.update(
                {
                    "product_spec_source_key": product_source_key,
                    "order_source_key": order_keys.get(link_key),
                    "item_no": item_no,
                    "customer": _mapped_cell(cells, criteria_mapping, "客户").display_text,
                    "category": _mapped_cell(cells, criteria_mapping, "类别").display_text,
                    "version": _mapped_cell(cells, criteria_mapping, "版本").display_text,
                    "inspection_item": inspection_item,
                    "lower_limit": _mapped_cell(cells, criteria_mapping, "下限").display_text,
                    "upper_limit": _mapped_cell(cells, criteria_mapping, "上限").display_text,
                    "unit": _mapped_cell(cells, criteria_mapping, "单位").display_text,
                    "project_no": project_no,
                    "order_no": order_no,
                }
            )
            records.append(record)
    return records


def _parse_material_issue(sheet, header_row, mapping, sha256, issues):
    records = []
    for row_no, cells in _sheet_row_iter(sheet, header_row):
        order_no = _mapped_cell(cells, mapping, "独立需求号", "订单号").display_text
        if not any(
            (
                order_no,
                _mapped_cell(cells, mapping, "成品品名").display_text,
                _mapped_cell(cells, mapping, "重量").display_text,
            )
        ):
            continue
        weight = _decimal_text(
            _mapped_cell(cells, mapping, "重量", "重量kg"),
            issues,
            sheet=sheet.name,
            row=row_no,
            field="weight_kg",
            required=True,
        )
        if not order_no:
            issues.append(_issue("error", "订单号不能为空。", sheet=sheet.name, row=row_no, field="order_no"))
        record = _record_base(
            sha256, sheet.name, row_no, RECORD_RECEIPT, _raw_row(cells, mapping)
        )
        record.update(
            {
                "order_no": order_no,
                "item_no": _mapped_cell(cells, mapping, "项次").display_text,
                "finished_product_name": _mapped_cell(cells, mapping, "成品品名").display_text,
                "specification": _mapped_cell(cells, mapping, "成品规格", "规格").display_text,
                "material": _mapped_cell(cells, mapping, "材质").display_text,
                "batch_no": _mapped_cell(cells, mapping, "批号").display_text,
                "sheet_size": _mapped_cell(cells, mapping, "出片尺寸").display_text,
                "weight_kg": weight,
                "manufactured_on": _date_value(_mapped_cell(cells, mapping, "制造时间", "制造日期")),
            }
        )
        records.append(record)
    return records


def _link_material_receipt_orders(records, issues):
    for record in records:
        if record.get("record_type") != RECORD_RECEIPT:
            continue
        queryset = QualityOrder.objects.filter(order_no=record.get("order_no", ""))
        if record.get("item_no"):
            queryset = queryset.filter(item_no=record["item_no"])
        matches = list(queryset.order_by("id")[:3])
        if len(matches) > 1 and record.get("specification"):
            refined = queryset.filter(specification__iexact=record["specification"])
            if record.get("material"):
                refined = refined.filter(material__iexact=record["material"])
            matches = list(refined.order_by("id")[:3])
        if len(matches) == 1:
            record["order_id"] = matches[0].pk
        elif not matches:
            issues.append(
                _issue(
                    "warning",
                    "未找到对应订单明细，收料记录仍会保留，但暂不计入具体订单的已收胶料。",
                    sheet=record["sheet"],
                    row=record["row"],
                    field="order",
                )
            )
        else:
            issues.append(
                _issue(
                    "warning",
                    "存在多条可能对应的订单明细，收料记录暂不自动关联，请在线确认。",
                    sheet=record["sheet"],
                    row=record["row"],
                    field="order",
                )
            )


def parse_business_sheets(sheets, sha256):
    issues = []
    material_match = None
    product_match = None
    internal_matches = []
    factory_main = False
    factory_criteria = False
    for sheet in sheets:
        header_row, mapping = _find_header(
            sheet, ["独立需求号", "成品品名", "成品规格", "材质", "重量"]
        )
        if mapping:
            material_match = (sheet, header_row, mapping)
        header_row, mapping = _find_header(
            sheet, ["规格", "材质", "料长", "切料重", "一次加硫条件"]
        )
        if mapping and "订单量" not in mapping:
            product_match = (sheet, header_row, mapping)
        header_row, mapping = _find_header(
            sheet, ["订单编号", "规格", "胶料配方", "交期", "订单量"]
        )
        if mapping:
            internal_matches.append((sheet, header_row, mapping))
        if _find_header(sheet, ["独立需求号", "项次", "材质", "规格", "订单量"])[1]:
            factory_main = True
        if _find_header(sheet, ["独立需求号", "项次", "项目号", "检验项目"])[1]:
            factory_criteria = True

    if material_match:
        sheet, header_row, mapping = material_match
        records = _parse_material_issue(sheet, header_row, mapping, sha256, issues)
        _link_material_receipt_orders(records, issues)
        source_type = BusinessImportBatch.SourceType.MATERIAL_ISSUE
    elif factory_main and factory_criteria:
        records = _parse_factory_work_contact(sheets, sha256, issues)
        source_type = BusinessImportBatch.SourceType.FACTORY_WORK_CONTACT
    elif product_match:
        sheet, header_row, mapping = product_match
        records = _parse_product_specifications(sheet, header_row, mapping, sha256, issues)
        source_type = BusinessImportBatch.SourceType.PRODUCT_SPECIFICATIONS
    elif internal_matches:
        records = _parse_internal_orders(sheets, sha256, issues)
        _link_existing_product_specifications(records, issues)
        source_type = BusinessImportBatch.SourceType.INTERNAL_ORDERS
    else:
        raise ValueError("无法识别Excel格式，请使用产品规格、内部订单、生产工作联络单或发料清单。")
    if not records:
        issues.append(_issue("error", "工作簿中没有可导入的有效业务行。"))
    return source_type, records, issues


def _existing_source_keys(records):
    by_type = {record_type: [] for record_type in RECORD_TYPES}
    for record in records:
        by_type[record["record_type"]].append(record["source_key"])
    return {
        RECORD_PRODUCT: set(
            ProductSpecification.objects.filter(source_key__in=by_type[RECORD_PRODUCT]).values_list(
                "source_key", flat=True
            )
        ),
        RECORD_ORDER: set(
            QualityOrder.objects.filter(source_key__in=by_type[RECORD_ORDER]).values_list(
                "source_key", flat=True
            )
        ),
        RECORD_RECEIPT: set(
            MaterialReceipt.objects.filter(source_key__in=by_type[RECORD_RECEIPT]).values_list(
                "source_key", flat=True
            )
        ),
        RECORD_CRITERION: set(
            ProductInspectionCriterion.objects.filter(
                source_key__in=by_type[RECORD_CRITERION]
            ).values_list("source_key", flat=True)
        ),
    }


def _preview_summary(record):
    if record["record_type"] == RECORD_PRODUCT:
        return " / ".join(
            item
            for item in (record.get("specification"), record.get("material"), record.get("mold_no"))
            if item
        )
    if record["record_type"] == RECORD_ORDER:
        return f"{record.get('order_no', '')} / {record.get('specification', '')} / {record.get('order_quantity') or ''}"
    if record["record_type"] == RECORD_RECEIPT:
        return f"{record.get('order_no', '')} / {record.get('batch_no', '')} / {record.get('weight_kg') or ''}kg"
    return f"{record.get('order_no', '')} / {record.get('inspection_item', '')}"


def preview_business_workbook(uploaded_file, user):
    data = uploaded_file.read()
    uploaded_file.seek(0)
    sha256 = hashlib.sha256(data).hexdigest()
    sheets, parser = read_business_workbook(data)
    source_type, records, issues = parse_business_sheets(sheets, sha256)
    existing = _existing_source_keys(records)
    for record in records:
        if record["source_key"] in existing[record["record_type"]]:
            record["action"] = "SKIP"
            issues.append(
                _issue(
                    "warning",
                    "该文件的此源行已导入，将跳过且不会覆盖在线修改。",
                    sheet=record["sheet"],
                    row=record["row"],
                    field="source_key",
                )
            )
        else:
            record["action"] = "CREATE"
    errors = [item for item in issues if item["level"] == "error"]
    warnings = [item for item in issues if item["level"] == "warning"]
    batch = BusinessImportBatch(
        source_type=source_type,
        parser=parser,
        original_name=str(getattr(uploaded_file, "name", "upload.xlsx"))[:255],
        sha256=sha256,
        payload={"rows": json_safe(records), "parser": parser},
        errors=errors,
        warnings=warnings,
        created_by=user,
    )
    batch.original_file.save(batch.original_name, ContentFile(data), save=False)
    batch.save()
    counts = {
        "product_specifications": sum(r["record_type"] == RECORD_PRODUCT for r in records),
        "orders": sum(r["record_type"] == RECORD_ORDER for r in records),
        "material_receipts": sum(r["record_type"] == RECORD_RECEIPT for r in records),
        "inspection_criteria": sum(r["record_type"] == RECORD_CRITERION for r in records),
    }
    error_rows = {(item.get("sheet"), item.get("row")) for item in errors}
    return {
        "token": str(batch.pk),
        "source_type": source_type,
        "total_rows": len(records),
        "counts": counts,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "rows": [
            {
                "row_key": record["row_key"],
                "record_type": record["record_type"],
                "sheet": record["sheet"],
                "row": record["row"],
                "action": record["action"],
                "order_no": record.get("order_no", ""),
                "item_no": record.get("item_no", ""),
                "specification": record.get("specification", ""),
                "material": record.get("material", ""),
                "summary": _preview_summary(record),
                "valid": (record["sheet"], record["row"]) not in error_rows,
            }
            for record in records
        ],
        "issues": issues,
    }


def _date_from_payload(value):
    return parse_date(value) if value else None


def _decimal_from_payload(value):
    return Decimal(value) if value not in (None, "") else None


def _find_order_for_receipt(record, imported_orders):
    if record.get("order_id"):
        order = QualityOrder.objects.filter(pk=record["order_id"]).first()
        if (
            order is not None
            and order.order_no == record.get("order_no", "")
            and (
                not record.get("item_no")
                or order.item_no == record.get("item_no", "")
            )
        ):
            return order
    order_source_key = record.get("order_source_key")
    if order_source_key and order_source_key in imported_orders:
        return imported_orders[order_source_key]
    queryset = QualityOrder.objects.filter(order_no=record["order_no"])
    if record.get("item_no"):
        queryset = queryset.filter(item_no=record["item_no"])
    matches = list(queryset.order_by("id")[:2])
    return matches[0] if len(matches) == 1 else None


def commit_business_batch(batch, user):
    try:
        with transaction.atomic():
            current = BusinessImportBatch.objects.filter(pk=batch.pk).first()
            if current is None or current.created_by_id != user.pk:
                raise ValueError("无权提交该导入批次。")
            if current.status != BusinessImportBatch.Status.PREVIEWED:
                raise ValueError("该导入批次已经提交或正在提交，不能重复导入。")
            if current.errors:
                raise ValueError("预检存在错误，请修正Excel后重新上传。")
            claimed = BusinessImportBatch.objects.filter(
                pk=current.pk,
                created_by=user,
                status=BusinessImportBatch.Status.PREVIEWED,
            ).update(status=BusinessImportBatch.Status.COMMITTING)
            if claimed != 1:
                raise ValueError("该导入批次已经提交或正在提交，不能重复导入。")
            current.refresh_from_db()
            records = current.payload.get("rows", [])
            existing = _existing_source_keys(records)
            imported = {key: 0 for key in ("product_specifications", "orders", "material_receipts", "inspection_criteria")}
            skipped = dict(imported)
            product_specs = {
                item.source_key: item
                for item in ProductSpecification.objects.filter(
                    source_key__in=existing[RECORD_PRODUCT]
                )
            }
            imported_orders = {
                item.source_key: item
                for item in QualityOrder.objects.filter(source_key__in=existing[RECORD_ORDER])
            }

            for record in records:
                if record["record_type"] != RECORD_PRODUCT:
                    continue
                if record["source_key"] in existing[RECORD_PRODUCT]:
                    skipped["product_specifications"] += 1
                    continue
                product = ProductSpecification(
                    product_name=record.get("product_name", ""),
                    customer_product_no=record.get("customer_product_no", ""),
                    specification=record.get("specification", ""),
                    material=record.get("material", ""),
                    material_length=record.get("material_length", ""),
                    cut_weight=record.get("cut_weight", ""),
                    strip_count=record.get("strip_count", ""),
                    primary_curing=record.get("primary_curing", ""),
                    secondary_curing=record.get("secondary_curing", ""),
                    total_cavities=record.get("total_cavities", ""),
                    effective_cavities=record.get("effective_cavities", ""),
                    mold_in_stock=record.get("mold_in_stock", ""),
                    mold_no=record.get("mold_no", ""),
                    mold_size=record.get("mold_size", ""),
                    standard_hours=record.get("standard_hours", ""),
                    notes=record.get("notes", ""),
                    source_batch=current,
                    source_sheet=record["sheet"],
                    source_row=record["row"],
                    source_key=record["source_key"],
                    raw_data=record.get("raw_data", {}),
                )
                product.save()
                record_revision(
                    product,
                    user,
                    BusinessRecordRevision.Action.IMPORT,
                    source_batch=current,
                )
                product_specs[record["source_key"]] = product
                imported["product_specifications"] += 1

            for record in records:
                if record["record_type"] != RECORD_ORDER:
                    continue
                if record["source_key"] in existing[RECORD_ORDER]:
                    skipped["orders"] += 1
                    continue
                product_spec = product_specs.get(record.get("product_spec_source_key"))
                if product_spec is None and record.get("product_spec_id"):
                    product_spec = ProductSpecification.objects.filter(
                        pk=record["product_spec_id"], is_active=True
                    ).first()
                order = QualityOrder(
                    order_no=record.get("order_no", ""),
                    item_no=record.get("item_no", ""),
                    batch_no=record.get("batch_no", ""),
                    product_code=record.get("product_code", ""),
                    product_name=record.get("product_name", ""),
                    specification=record.get("specification", ""),
                    material=record.get("material", ""),
                    product_specification=product_spec,
                    order_quantity=record.get("order_quantity"),
                    order_date=_date_from_payload(record.get("order_date")),
                    due_date=_date_from_payload(record.get("due_date")),
                    mold_size=record.get("mold_size", ""),
                    forming_hours=_decimal_from_payload(record.get("forming_hours")),
                    production_required=record.get("production_required"),
                    legacy_shipment_text=record.get("legacy_shipment_text", ""),
                    required_material_kg=_decimal_from_payload(
                        record.get("required_material_kg")
                    ),
                    status=record.get("status", QualityOrder.Status.OPEN),
                    notes=record.get("notes", ""),
                    source_batch=current,
                    source_sheet=record["sheet"],
                    source_row=record["row"],
                    source_key=record["source_key"],
                    raw_data=record.get("raw_data", {}),
                    created_by=user,
                )
                order.save()
                record_revision(
                    order,
                    user,
                    BusinessRecordRevision.Action.IMPORT,
                    source_batch=current,
                )
                imported_orders[record["source_key"]] = order
                imported["orders"] += 1

            for record in records:
                if record["record_type"] == RECORD_CRITERION:
                    if record["source_key"] in existing[RECORD_CRITERION]:
                        skipped["inspection_criteria"] += 1
                        continue
                    product = product_specs.get(record.get("product_spec_source_key"))
                    if product is None:
                        raise ValueError("检验标准对应的产品规格不存在，请重新预检。")
                    order = imported_orders.get(record.get("order_source_key"))
                    criterion = ProductInspectionCriterion(
                        product_specification=product,
                        order=order,
                        item_no=record.get("item_no", ""),
                        project_no=record.get("project_no", ""),
                        customer=record.get("customer", ""),
                        category=record.get("category", ""),
                        version=record.get("version", ""),
                        inspection_item=record.get("inspection_item", ""),
                        lower_limit=record.get("lower_limit", ""),
                        upper_limit=record.get("upper_limit", ""),
                        unit=record.get("unit", ""),
                        source_batch=current,
                        source_sheet=record["sheet"],
                        source_row=record["row"],
                        source_key=record["source_key"],
                        raw_data=record.get("raw_data", {}),
                    )
                    criterion.save()
                    record_revision(
                        criterion,
                        user,
                        BusinessRecordRevision.Action.IMPORT,
                        source_batch=current,
                    )
                    imported["inspection_criteria"] += 1
                elif record["record_type"] == RECORD_RECEIPT:
                    if record["source_key"] in existing[RECORD_RECEIPT]:
                        skipped["material_receipts"] += 1
                        continue
                    receipt = MaterialReceipt(
                        order=_find_order_for_receipt(record, imported_orders),
                        order_no=record.get("order_no", ""),
                        item_no=record.get("item_no", ""),
                        finished_product_name=record.get("finished_product_name", ""),
                        specification=record.get("specification", ""),
                        material=record.get("material", ""),
                        batch_no=record.get("batch_no", ""),
                        sheet_size=record.get("sheet_size", ""),
                        weight_kg=_decimal_from_payload(record.get("weight_kg")),
                        manufactured_on=_date_from_payload(record.get("manufactured_on")),
                        source_batch=current,
                        source_sheet=record["sheet"],
                        source_row=record["row"],
                        source_key=record["source_key"],
                        raw_data=record.get("raw_data", {}),
                    )
                    receipt.save()
                    record_revision(
                        receipt,
                        user,
                        BusinessRecordRevision.Action.IMPORT,
                        source_batch=current,
                    )
                    imported["material_receipts"] += 1

            committed_at = timezone.now()
            changed = BusinessImportBatch.objects.filter(
                pk=current.pk, status=BusinessImportBatch.Status.COMMITTING
            ).update(status=BusinessImportBatch.Status.COMMITTED, committed_at=committed_at)
            if changed != 1:
                raise ValueError("导入批次状态已变化，请刷新后确认结果。")
            return {"imported": imported, "skipped": skipped}
    except ValueError:
        raise
    except DjangoValidationError as exc:
        if hasattr(exc, "message_dict"):
            message = "；".join(
                f"{field}：{'；'.join(messages)}" for field, messages in exc.message_dict.items()
            )
        else:
            message = "；".join(exc.messages)
        raise ValueError(f"提交数据校验失败：{message}") from exc
    except IntegrityError as exc:
        raise ValueError("数据已被其他操作导入或占用，请重新预检。") from exc
    except OperationalError as exc:
        raise ValueError("数据库正忙，导入未执行，请稍后重试。") from exc


TEMPLATE_HEADERS = {
    "product_specifications": [
        "产品名称",
        "客户产品号",
        "规格",
        "材质",
        "料长",
        "切料重",
        "条数",
        "一次加硫条件",
        "二次加硫条件",
        "总孔数",
        "有效孔数",
        "模具在库",
        "模具号",
        "模具尺寸",
        "标准工时",
        "备注",
    ],
    "orders": [
        "订单编号",
        "项次",
        "产品名称",
        "规格",
        "胶料配方",
        "交期",
        "订单量",
        "成型工时",
        "下单时间",
        "模具尺寸",
        "是否生产",
        "出货信息",
        "所需胶料",
    ],
    "material_receipts": [
        "序号",
        "项次",
        "独立需求号",
        "成品品名",
        "成品规格",
        "材质",
        "批号",
        "出片尺寸",
        "重量",
        "制造时间",
    ],
}


def create_business_template(kind="product_specifications"):
    if kind not in TEMPLATE_HEADERS:
        raise ValueError("无效的模板类型。")
    workbook = Workbook()
    sheet = workbook.active
    titles = {
        "product_specifications": "产品规格",
        "orders": "订单",
        "material_receipts": "胶料收料",
    }
    sheet.title = titles[kind]
    sheet.append(TEMPLATE_HEADERS[kind])
    header_fill = PatternFill("solid", fgColor="1677FF")
    for cell in sheet[1]:
        cell.font = Font(color="FFFFFF", bold=True)
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(TEMPLATE_HEADERS[kind]))}1"
    for index, header in enumerate(TEMPLATE_HEADERS[kind], start=1):
        sheet.column_dimensions[get_column_letter(index)].width = min(max(len(header) * 2 + 4, 12), 28)
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def _safe_excel_text(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value


def create_business_error_report(batch):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "预检问题"
    sheet.append(["级别", "工作表", "行号", "字段", "说明"])
    for issue in [*batch.errors, *batch.warnings]:
        sheet.append(
            [
                _safe_excel_text(issue.get("level", "")),
                _safe_excel_text(issue.get("sheet", "")),
                issue.get("row", ""),
                _safe_excel_text(issue.get("field", "")),
                _safe_excel_text(issue.get("message", "")),
            ]
        )
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1677FF")
    sheet.freeze_panes = "A2"
    for index, width in enumerate((12, 24, 10, 24, 70), start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()
