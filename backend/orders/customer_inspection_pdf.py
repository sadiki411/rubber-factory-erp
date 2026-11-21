"""Standalone attachment, not a redesign of the frozen production card."""
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from flowcards.pdf import font_name


def condition_text(label, data):
    state = data.get("state")
    if state == "NOT_REQUIRED":
        return f"{label}：不需要二烤"
    if state != "REFERENCE":
        return f"{label}：{data.get('raw') or '原表未提供'}"

    def bounds(a, b):
        return a if a == b else f"{a} - {b}"

    time = bounds(data["time_lower"], data["time_upper"])
    temp = bounds(data["temperature_lower"], data["temperature_upper"])
    note = "（原表单位误写，按秒读取，数值不换算）" if data.get("corrected_unit") else ""
    return f"{label}：{time} {data['unit']} / {temp} °C{note}"


def render(payloads):
    buffer = BytesIO()
    font = font_name()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=14*mm, rightMargin=14*mm,
                            topMargin=14*mm, bottomMargin=15*mm, title="订单客户检验标准")
    base = ParagraphStyle("CustomerInspection", fontName=font, fontSize=9.5, leading=14, wordWrap="CJK")
    title = ParagraphStyle("InspectionTitle", parent=base, fontSize=16, leading=22, spaceAfter=8)
    story = [Paragraph("订单客户检验标准 · 翔邦原表", title)]

    def line(text):
        return Paragraph(escape(str(text).replace("℃", "°C")), base)

    def table(rows, widths):
        wrapped = [[line(value) for value in row] for row in rows]
        grid = Table(wrapped, colWidths=[width*mm for width in widths], repeatRows=1, hAlign="LEFT")
        grid.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9edf1")),
            ("LINEBELOW", (0, 0), (-1, 0), .5, colors.HexColor("#85919b")),
            ("LINEBELOW", (0, 1), (-1, -1), .3, colors.HexColor("#c6ced6")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        return grid

    labels = {"PENDING": "原表待核对", "CONFIRMED": "来源内容已核对", "CONFLICT": "来源有冲突，待核",
              "INHERITED": "沿用已确认版，本单未附检验表"}
    for payload in payloads:
        story += [
            line(f"订单/项次：{payload['order_no']} / {payload['item_no']}    客户：{payload['customer_code'] or '待核'}"),
            line(f"规格 / 材质：{payload['specification']} / {payload['material']}    项目号：{payload['project_no'] or '待核'}"),
            line(f"来源：{payload['source']}　协力商：{payload['supplier'] or '未提供'}　状态：{labels.get(payload['status'], '待核')}"),
            line("原样保留各类别，不指定代加工检验工序，不作为本批检验结论或放行依据。"),
        ]
        ref = payload.get("reference", {})
        for key, name in (("primary", "一次加硫"), ("secondary", "二烤")):
            story.append(line(condition_text(name, ref.get(key, {}))))
        story.append(line(f"原表：模具 {ref.get('mold_no') or '未提供'} / {ref.get('mold_size') or '未提供'}；料长 {ref.get('material_length') or '未提供'}；切料重 {ref.get('cut_weight') or '未提供'}"))
        story += [line(problem) for problem in payload.get("problems", [])]
        story.append(Spacer(1, 4*mm))
        rows = [["客户 / 类别 / 版号", "检验项目", "下限", "上限", "单位", "原始出处"]]
        for group in payload["groups"]:
            for row in group["rows"]:
                rows.append([f"{group['customer_code']} / {group['category']} / {group['version']}", row["item"],
                             row["lower"] or "未提供", row["upper"] or "未提供", row["unit"] or "未提供",
                             f"{row['file']} {row['sheet']} 第{row['row'] or '—'}行"])
        story += [table(rows, [36, 29, 20, 20, 17, 60]), Spacer(1, 6*mm)]

    def footer(canvas, document):
        canvas.setFont(font, 8)
        canvas.drawRightString(A4[0]-14*mm, 8*mm, f"第 {document.page} 页")

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
