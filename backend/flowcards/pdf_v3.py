"""Review v3: explicit limits and paper identity. v1/v2 stay frozen."""
import io
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.graphics import renderPDF
from .pdf import font_name, wrap
from .limits import limit_text, limit_mode


def display_text(value):
    # Some CJK TTC fonts expose U+2103 without a drawable glyph. Keep the
    # business snapshot untouched and print the equivalent degree+C instead.
    return str(value).replace("℃", "°C")


def compact_identity(product, font, width):
    values = [product.get('customer_product_no') or product.get('name') or '未登记', product.get('material') or '未登记', product['specification']]
    lengths = [18, 12, 22]
    while True:
        parts = [v if len(v) <= n else v[:n-1] + '…' for v, n in zip(values, lengths)]
        text = f'编号 {parts[0]}  材质 {parts[1]}  规格 {parts[2]}'
        if len(wrap(display_text(text), width, font, 9)) == 1:
            return text
        target = max(range(3), key=lambda i: lengths[i])
        if lengths[target] <= 4:
            raise ValueError('纸张过窄，无法保留产品身份。')
        lengths[target] -= 1


def render_cards_v3(items, *, applicable_dimensions=False):
    out, font = io.BytesIO(), font_name()
    sizes = {(c.group.snapshot["template"].get("width_mm", 190), c.group.snapshot["template"].get("height_mm", 130)) for c, _ in items}
    if len(sizes) != 1:
        raise ValueError("不同纸张尺寸请分开打印。")
    width, height = sizes.pop()
    if not 180 <= width <= 220 or not 125 <= height <= 165:
        raise ValueError("纸张尺寸超出已支持的180~220 × 125~165mm；不自动缩字。")
    c = canvas.Canvas(out, pagesize=(width*mm, height*mm), pageCompression=1, invariant=1)
    c.setTitle("工序流转卡 · 隔离审阅布局")
    for card, reprint in items:
        snap, extra = card.group.snapshot, []
        right, span = width-5, width-10
        signer_x, line_x = width-45, width-27

        def text(x, y, value, size=9, bold=False, center=False):
            value = display_text(value)
            c.setFont(font, size)
            c.setFillColorRGB(0, 0, 0)
            if bold:
                c.saveState()
                c.setLineWidth(.15)
                obj = c.beginText(); obj.setTextOrigin(x*mm, (height-y)*mm); obj.setTextRenderMode(2); obj.textLine(str(value)); obj.setTextRenderMode(0); c.drawText(obj)
                c.restoreState()
            else:
                (c.drawCentredString if center else c.drawString)(x*mm, (height-y)*mm, str(value))

        def box(x, y, w, h, shade=False):
            c.setLineWidth(.45)
            c.setFillColorRGB(.93, .93, .93) if shade else c.setFillColorRGB(1, 1, 1)
            c.rect(x*mm, (height-y-h)*mm, w*mm, h*mm, fill=1 if shade else 0)

        def field(x, y, w, h, value, size=9):
            value = display_text(value)
            rows = wrap(value, (w-2)*mm, font, size)
            if len(rows)*(size+1) > (h-1)*mm:
                extra.append(str(value)); rows = ["详见本卡续页"]
            start = y+(h-len(rows)*(size+1)/mm)/2+size/mm
            for i, row in enumerate(rows): text(x+1, start+i*(size+1)/mm, row, size)

        def line(x, y, length):
            c.setLineWidth(.45); c.line(x*mm, (height-y)*mm, (x+length)*mm, (height-y)*mm)

        def signer(y, label):
            text(signer_x, y, label); line(line_x, y+.7, 20)

        qr = QrCodeWidget(f"ERPFC:{card.pk}")
        b = qr.getBounds(); size = 17*mm
        drawing = Drawing(size, size, transform=[size/(b[2]-b[0]), 0, 0, size/(b[3]-b[1]), 0, 0]); drawing.add(qr)
        renderPDF.draw(drawing, c, 5*mm, (height-19)*mm)
        # Keep the entire date segment together. Never slice the assigned ID.
        parts = card.card_no.rsplit("-", 1)
        text(24, 8, parts[0] + ("-" if len(parts) > 1 else ""), 8.9)
        text(24, 13, parts[1] if len(parts) > 1 else "", 8.9)
        text(width/2+8, 13, "工序流转卡", 17.5, center=True)
        text(width-44, 8, f"日期 {card.original_date}", 8.9)
        text(width-44, 15, f"{card.planned_molds}M | {card.sequence}/{card.denominator}批", 11)
        p = snap["product"]
        ss = (snap.get("standard") or {}).get("snapshot", {})
        criteria = ss.get("criteria", [])
        pending = (not snap.get("standard") or ss.get("dimension_count") is None or
                   ss.get("bake", {}).get("state", "UNKNOWN") == "UNKNOWN" or
                   any(limit_mode(e) != "NONE" and (limit_mode(e) == "PENDING" or not e.get("unit") or not e.get("method")) for e in criteria) or
                   any(not any(e['stage'] == stage for e in criteria) for stage in ('UPPER_TEMP', 'LOWER_TEMP', 'MOLDING_HARDNESS', 'BAKED_HARDNESS')) or
                   any(ss.get("hardness", {}).get(stage, {}).get("object", "UNKNOWN") == "UNKNOWN" and
                       any(e['stage'] == stage and limit_mode(e) != 'NONE' for e in criteria)
                       for stage in ("MOLDING_HARDNESS", "BAKED_HARDNESS")))
        identity = f"产品编号 {p.get('customer_product_no') or p.get('name') or '未登记'}    规格 {p['specification']}    材质 {p['material'] or '未登记'}"
        if len(wrap(display_text(identity), (span-4)*mm, font, 9)) > 1:
            extra.append(identity)
            identity = compact_identity(p, font, (span-4)*mm)

        y = 21
        for value in [identity,
                      "订单/项次 " + "、".join(f"{o['order_no']}/{o['item_no'] or '-'}" for o in snap["orders"]) + "    客户 " + "、".join(sorted({o.get('customer') or '未确认' for o in snap["orders"]}))]:
            box(5,y,span,6); field(6,y,span-2,6,value); y+=6
        box(5,y,span,6)
        field(6,y,span-49,6,f"检验依据 {'v'+str(snap['standard']['version']) if snap.get('standard') else '待确认'}{' · 有项目待确认' if pending else ''}")
        signer(y+4.2,"机台员"); y+=6
        box(5,y,span,5,True); text(7,y+3.8,"生产实测",10,True); text(75,y+3.8,"时间"); line(85,y+4.2,28); signer(y+3.8,"测量人"); y+=5
        ss = (snap.get("standard") or {}).get("snapshot", {})
        criteria = ss.get("criteria", [])
        def criterion(stage, n=0):
            rows=[e for e in criteria if e["stage"] == stage]
            return rows[n] if n < len(rows) else None
        def limits(row):
            if not row: return "待确认"
            return limit_text(row)
        objects={"PRODUCT":"成品", "COUPON":"胶片", "UNKNOWN":"对象待定"}
        def object_label(stage): return objects.get(ss.get("hardness",{}).get(stage,{}).get("object","UNKNOWN"),"对象待确认")
        dimensions=[e for e in criteria if e["stage"] == "DIMENSION" and limit_mode(e) != "NONE"]
        needed=ss.get("dimension_count")
        dimension_count = max(len(dimensions), needed or 0)
        columns=[("上模实测温度",limits(criterion("UPPER_TEMP"))), ("下模实测温度",limits(criterion("LOWER_TEMP"))), (f"成型硬度·{object_label('MOLDING_HARDNESS')}",limits(criterion("MOLDING_HARDNESS")))]
        for i in range(min(2,dimension_count)):
            row=(dimensions[i] if i < len(dimensions) else None) if applicable_dimensions else criterion("DIMENSION",i)
            columns.append((row["name"] if row else f"关键尺寸{i+1}", limits(row)))
        if not dimension_count and needed is None:
            columns.append(("关键尺寸", "是否需要待确认"))
        col_width=(span-16)/len(columns); x=5
        for i,(heading,value) in enumerate([("项目","标准"),*columns]):
            w=16 if i == 0 else col_width
            for dy,h,content in [(0,6,heading),(6,6,value),(12,9,"实测" if i == 0 else "")]:
                box(x,y+dy,w,h,dy==0); field(x,y+dy,w,h,content,8.9)
            x+=w
        for row in dimensions[2:]: extra.append(f"关键尺寸 {row['name']} · 标准 {limits(row)} · 方法 {row['method']} · 实测 ____________________")
        if needed and needed > max(2,len(dimensions)): extra.append(f"需测尺寸共{needed}项，尚缺{needed-len(dimensions)}项确认要求；不自动当不适用。")
        y+=21
        box(5,y,span,8); field(6,y,span-48,8,"外观 □正常  "+"  ".join("□"+e["name"] for e in snap.get("appearance",[]))+" □其他____",8.9); signer(y+5.2,"巡检"); y+=8
        if snap["whole_mold"] == "REQUIRED":
            box(5,y,span,7,True); text(7,y+4.8,"整模检验",10,True); text(40,y+4.8,"定点"); line(49,y+5.4,20); text(71,y+4.8,"孔/模"); signer(y+4.8,"整模员"); y+=7
        else:
            text(7,y+3.6,"整模：不需",8.9); y+=5
        box(5,y,span,8); text(7,y+5.2,"毛边 □完成 □待处理"); box(64,y+1,span-112,6,True); text(66,y+5.2,"洒油 □正常 □少量 □否",10,True); signer(y+5.2,"毛边员"); y+=8
        box(5,y,span,4,True); text(7,y+3,"二次加硫（二烤）",9,True); y+=4
        bake=ss.get("bake",{})
        advice="不适用" if bake.get("state")=="NOT_APPLICABLE" else "待确认"
        if bake.get("state")=="APPLICABLE": advice=f"{bake['temperature_lower']}~{bake['temperature_upper']}℃ × {bake['minutes_lower']}~{bake['minutes_upper']}min"
        box(5,y,span,6); box(5,y,span/2,6); field(6,y,span/2-2,6,f"建议 {advice}"); text(7+span/2,y+4.2,"实际"); line(18+span/2,y+4.8,17); text(36+span/2,y+4.2,"℃ ×"); line(48+span/2,y+4.8,16); text(65+span/2,y+4.2,"min"); y+=6
        box(5,y,span,6); text(7,y+4.2,"日期"); line(18,y+4.8,37); text(59,y+4.2,"盘号"); line(70,y+4.8,span-119); signer(y+4.2,"二烤员"); y+=6
        box(5,y,span,6); field(6,y,span/2-2,6,f"烤后硬度 · {object_label('BAKED_HARDNESS')}  标准 {limits(criterion('BAKED_HARDNESS'))}",8.9); text(7+span/2,y+4.2,"实际"); line(19+span/2,y+4.8,span/2-24); y+=6
        box(5,y,span,6); text(7,y+4.2,"取样盘"); line(23,y+4.8,28); text(55,y+4.2,"测量时间"); line(76,y+4.8,span-125); signer(y+4.2,"测量人"); y+=6
        text(5,height-6,f"随货流转 · 异常同号续记 · 表单版次：{snap['template'].get('version','草稿')}",7.8); signer(height-5,"品检员")
        if reprint: text(5,height-2,f"补打{reprint}次",7)
        if extra: text(80,height-2,"含身份/标准续页，须随本卡保存",7)
        c.showPage()
        if extra:
            text(5,10,f"工序流转卡续页 · {card.card_no}",11); yy=18
            for value in extra:
                for row in wrap(display_text(value),(span-2)*mm,font,10):
                    if yy>height-10:
                        c.showPage(); text(5,10,f"续页 · {card.card_no}",11); yy=18
                    text(6,yy,row,10); yy+=5
                yy+=3
            c.showPage()
    c.save()
    return out.getvalue()
