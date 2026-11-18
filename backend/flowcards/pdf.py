"""Deterministic snapshot-only renderer. Downloading never changes print counters."""
import io
import os
from pathlib import Path

from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.graphics import renderPDF


def font_name():
    if "FlowCJK" not in pdfmetrics.getRegisteredFontNames():
        candidates = [os.getenv("FLOW_CARDS_FONT", ""),
                      "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", "C:/Windows/Fonts/simhei.ttf"]
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                pdfmetrics.registerFont(TTFont("FlowCJK", candidate))
                break
        else:
            raise RuntimeError("工序卡PDF缺少可嵌入中文字体，请配置FLOW_CARDS_FONT。")
    return "FlowCJK"


def wrap(text, width, font, size=9):
    lines, line = [], ""
    for char in str(text).replace("\r", ""):
        if char == "\n":
            lines.append(line)
            line = ""
        elif pdfmetrics.stringWidth(line + char, font, size) > width:
            lines.append(line)
            line = char
        else:
            line += char
    return lines + ([line] if line else [])


def render_cards(items):
    items = list(items)
    renderers = {card.group.snapshot.get("template", {}).get("renderer") for card, _ in items}
    if renderers == {"flowcard-review-20261118-v2"}:
        from .pdf_v2 import render_cards_v2
        return render_cards_v2(items)
    if renderers != {"flowcard-draft-20261118-v1"}:
        raise ValueError("不同渲染版本请分开打印；不能替换旧卡模板。")
    return render_cards_v1(items)


def render_cards_v1(items):
    out = io.BytesIO()
    font = font_name()
    c = canvas.Canvas(out, pagesize=(190 * mm, 130 * mm), pageCompression=1, invariant=1)
    c.setTitle("工序流转卡 - 每卡独立身份，计划不计产量")
    for card, reprint in items:
        snap = card.group.snapshot
        template = snap.get("template", {})
        if template.get("renderer") != "flowcard-draft-20261118-v1":
            raise ValueError("此模板渲染版本不可用；不能用当前模板冒充原卡。")
        extra = []

        def text(x, y, value, size=9, centered=False):
            c.setFont(font, size)
            (c.drawCentredString if centered else c.drawString)(x * mm, (130-y) * mm, str(value))

        def box(x, y, w, h):
            c.setLineWidth(.45)
            c.rect(x*mm, (130-y-h)*mm, w*mm, h*mm)

        def field(x, y, w, h, value, size=9, center=False):
            lines = wrap(value, (w-2)*mm, font, size)
            max_lines = max(1, int((h-1)*mm/(size+1)))
            if len(lines) > max_lines:
                extra.append(str(value))
                lines = ["详见本卡续页"]
            for i, line in enumerate(lines):
                text(x+w/2 if center else x+1, y+((h-(len(lines)*(size+1)/mm))/2)+size/mm+i*(size+1)/mm,
                     line, size, center)

        qr = QrCodeWidget(f"ERPFC:{card.pk}")
        bounds = qr.getBounds()
        drawing = Drawing(14*mm, 14*mm, transform=[14*mm/(bounds[2]-bounds[0]), 0, 0, 14*mm/(bounds[3]-bounds[1]), 0, 0])
        drawing.add(qr)
        renderPDF.draw(drawing, c, 5*mm, 110*mm)
        text(21, 8, card.card_no[:12], 8.9)
        text(21, 13, card.card_no[12:], 8.9)
        text(100, 13, "工序流转卡", 17.5, True)
        text(148, 8, f"日期 {card.original_date}", 8.9)
        text(148, 15, f"{card.planned_molds}M | {card.sequence}/{card.denominator}批", 11)
        p = snap["product"]
        y = 21
        for label in [f"产品编号 {p.get('customer_product_no') or p.get('name') or '未登记'}    规格 {p['specification']}    材质 {p['material'] or '未登记'}",
                      "订单/项次 " + "、".join(f"{o['order_no']}/{o['item_no'] or '-'}" for o in snap["orders"]) + "    客户 " + "、".join(sorted({o.get('customer') or '未确认' for o in snap["orders"]})),
                      f"检验依据 {'已确认版本 '+str(snap['standard']['version']) if snap.get('standard') else '待确认'}    机台操作员 ____________________"]:
            box(5, y, 180, 6)
            field(7, y, 176, 6, label)
            y += 6
        box(5, y, 180, 5)
        text(7, y+3.8, "生产实测", 10)
        text(88, y+3.8, "时间 ______________    测量人 ____________")
        y += 5
        criteria = (snap.get("standard") or {}).get("snapshot", {}).get("criteria", [])

        def criterion(stage, index=0):
            found = [e for e in criteria if e["stage"] == stage]
            return found[index] if len(found) > index else None

        def limits(row):
            return f"{row['lower']}~{row['upper']} {row['unit']}" if row else "待确认"

        dimension1, dimension2 = criterion("DIMENSION"), criterion("DIMENSION", 1)
        for dimension in [e for e in criteria if e["stage"] == "DIMENSION"][2:]:
            extra.append(f"关键尺寸 {dimension['name']} · 标准 {limits(dimension)} · 方法 {dimension['method']} · 实测 ____________________")
        cols = [("项目", "标准"), ("上模实测温度", limits(criterion("UPPER_TEMP"))),
                ("下模实测温度", limits(criterion("LOWER_TEMP"))), ("成型后硬度", limits(criterion("MOLDING_HARDNESS"))),
                (dimension1["name"] if dimension1 else "关键尺寸①", limits(dimension1)),
                (dimension2["name"] if dimension2 else "关键尺寸②", limits(dimension2))]
        widths = [16] + [32.8]*5
        x = 5
        for i, (heading, standard) in enumerate(cols):
            w = widths[i]
            for offset, height, content in [(0, 5, heading), (5, 6, standard), (11, 10, "实测" if i == 0 else "")]:
                box(x, y+offset, w, height)
                field(x, y+offset, w, height, content, center=True)
            x += w
        y += 21
        box(5, y, 180, 8)
        problems = snap.get("appearance", [])
        names = [e["name"] for e in problems] if problems else ["毛刺", "杂质", "包风", "缺料"]
        field(6, y, 137, 8, "外观 □正常  " + "  ".join("□"+name for name in names) + "  □其他____", 8.9)
        text(145, y+5.3, "巡 检 __________")
        y += 8
        if snap["whole_mold"] == "REQUIRED":
            box(5, y, 180, 7)
            text(7, y+4.8, "整模检验    定点：________ 孔/模", 10)
            text(145, y+4.8, "整模员 _________")
            y += 7
        else:
            text(7, y+2.8, "整模：不需", 8.9)
            y += 4
        box(5, y, 180, 8)
        text(7, y+5.3, "毛边  □完成 □待处理", 9)
        box(65, y+1, 77, 6)
        text(67, y+5.3, "洒油 □正常 □少量 □否", 10)
        text(145, y+5.3, "毛边员 _________")
        y += 8
        bake = criterion("SECONDARY_CURE")
        baked = criterion("BAKED_HARDNESS")
        bake_config = (snap.get("standard") or {}).get("snapshot", {}).get("bake", {})
        bake_advice = "不适用" if bake_config.get("state") == "NOT_APPLICABLE" else limits(bake)
        if bake_config.get("state") == "APPLICABLE":
            bake_advice = f"{bake_config['temperature_lower']}~{bake_config['temperature_upper']}℃ × {bake_config['minutes_lower']}~{bake_config['minutes_upper']}min"
        for h, content in [
            (4, "二次加硫（二烤）"),
            (6, f"建议条件 {bake_advice}     实际条件 ______ ℃ × ______ min"),
            (6, "日期 __________  烤盘 ______ 至 ______"),
            (6, f"烤后硬度  标准 {limits(baked)}    实际 ____________________"),
            (6, "取样盘 ______    测量时间 ______________"),
        ]:
            box(5, y, 180, h)
            field(6, y, 178, h, content, 9)
            if content.startswith("日期"):
                text(145, y+4.2, "二烤员 _________")
            elif content.startswith("取样盘"):
                text(145, y+4.2, "测量人 _________")
            y += h
        text(5, 124, f"异常同号续记 · 纸卡不入炉 · 表单版次：{template.get('version', '草稿')}", 7.8)
        text(145, 125, "品 检 员 ________", 10)
        if reprint:
            text(5, 128, f"补打{reprint}次（原卡同一身份）", 7)
        if extra:
            text(85, 128, "含身份/标准续页，须随本卡保存", 7)
        c.showPage()
        if extra:
            text(5, 10, f"工序流转卡续页 · {card.card_no}", 11)
            yy = 18
            for value in extra:
                for line in wrap(value, 178*mm, font, 10):
                    if yy > 120:
                        c.showPage()
                        text(5, 10, f"续页 · {card.card_no}", 11)
                        yy = 18
                    text(6, yy, line, 10)
                    yy += 5
                yy += 3
            c.showPage()
    c.save()
    return out.getvalue()
