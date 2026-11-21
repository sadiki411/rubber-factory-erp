"""Shop-floor paper layout. Consume only the frozen card snapshot."""
from dataclasses import dataclass
from io import BytesIO

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics

from erp.inspection_labels import inspection_item_label
from .guidance import bake_text, oil_instruction, parameter_text, usable
from .limits import limit_text
from .measurements import applicable_criteria
from .paper import SINGLE, finish_card, make_canvas, start_card
from .pdf import font_name, wrap
from .pdf_v3 import display_text


@dataclass
class Cell:
    value: str
    share: float = 1
    size: float = 10.2
    align: str = "left"
    shade: bool = False
    writing: bool = False
    options: tuple = ()


class CardLayout:
    inset = 2.5

    def __init__(self, canvas, font, width, height):
        self.c, self.font = canvas, font
        self.width, self.height = width, height
        self.span = width - 2*self.inset
        self.top = self.inset
        self.bottom = height - self.inset

    def text_width(self, value, size):
        return pdfmetrics.stringWidth(display_text(value), self.font, size) / mm

    def text(self, x, y, value, size=10.2):
        self.c.setFont(self.font, size)
        self.c.setFillGray(0)
        self.c.drawString(x*mm, (self.height-y)*mm, display_text(value))

    def rect(self, x, y, width, height, shade=False):
        self.c.setStrokeGray(.15); self.c.setFillGray(.94 if shade else 1)
        self.c.setLineWidth(.48)
        self.c.rect(x*mm, (self.height-y-height)*mm, width*mm, height*mm, fill=int(shade))

    def writing_line(self, x, y, width):
        self.c.setStrokeGray(.42); self.c.setLineWidth(.45)
        self.c.line(x*mm, (self.height-y)*mm, (x+width)*mm, (self.height-y)*mm)

    def lines(self, value, width, size):
        return wrap(display_text(value), max(width, 1)*mm, self.font, size) or [""]

    def cell_units(self, cells):
        total = sum(cell.share for cell in cells)
        return [(cell, self.span*cell.share/total) for cell in cells]

    def option_lines(self, cell, width):
        rows, used = [[]], 0
        for label in cell.options:
            spaces = self.lines(label, width-9, cell.size)
            for i, part in enumerate(spaces):
                size = self.text_width(part, cell.size) + (6 if i == 0 else 3)
                if rows[-1] and (used+size > width-4 or i):
                    rows.append([]); used = 0
                rows[-1].append((part, i == 0, size))
                used += size
        return rows

    def height_for(self, cells, minimum):
        h = minimum
        for cell, width in self.cell_units(cells):
            if cell.options:
                count = len(self.option_lines(cell, width))
            else:
                allowance = 12 if cell.writing else 0
                count = len(self.lines(cell.value, width-4-allowance, cell.size))
            h = max(h, count*(cell.size+2)/mm+1.35)
        return h

    def row(self, y, cells, h):
        x = self.inset
        for cell, width in self.cell_units(cells):
            self.rect(x, y, width, h, cell.shade)
            lead = (cell.size+2)/mm
            if cell.options:
                parts = self.option_lines(cell, width)
                baseline = y+(h-len(parts)*lead)/2+cell.size/mm
                for i, cols in enumerate(parts):
                    xx, yy = x+2, baseline+i*lead
                    for label, checkbox, size in cols:
                        if checkbox:
                            self.rect(xx, yy-2.65, 2.7, 2.7)
                        self.text(xx+(3.6 if checkbox else 0), yy, label, cell.size)
                        xx += size
            else:
                lines = self.lines(cell.value, width-4-(12 if cell.writing else 0), cell.size)
                baseline = y+(h-len(lines)*lead)/2+cell.size/mm
                for idx, line in enumerate(lines):
                    dx = (width-self.text_width(line, cell.size))/2 if cell.align == "center" else 2
                    self.text(x+dx, baseline+idx*lead, line, cell.size)
                if cell.writing:
                    left = x+2+self.text_width(lines[-1],cell.size)+(1.5 if lines[-1] else 0)
                    self.writing_line(left, baseline+(len(lines)-1)*lead+.6, x+width-2-left)
            x += width
        return y+h

    def header(self, card, *, sample, preview):
        p = card.group.snapshot["product"]
        spec, material = p["specification"], p.get("material") or "材质未登记"
        code = p.get("customer_product_no") or p.get("name")
        identity = f"{spec}  /  {material}"
        left, right = 23, 37
        center = self.span-left-right
        size = 20 if self.text_width(identity,20) <= center-5 else 17
        headlines = self.lines(identity, center-5, size)
        labels = self.lines(f"产品编号 {code}", center-5, 9.4) if code else []
        overflow = []
        if len(headlines) > 2:
            headlines = [headlines[0], "完整规格与材质见续页"]
            overflow.append(f"规格：{spec}　材质：{material}")
        if len(labels) > 2:
            labels = ["完整产品编号见续页"]
            overflow.append(f"产品编号：{code}")
        number = self.lines(card.card_no, center-5, 9.5) if not sample else []
        h = max(22, 4 + len(headlines)*7.6 + (len(labels)+len(number))*4)
        x, y = self.inset, self.top
        self.rect(x,y,left,h); self.rect(x+left,y,center,h)
        self.rect(x+left+center,y,right,h)
        if not sample and not preview:
            qr = QrCodeWidget(f"ERPFC:{card.pk}")
            box = qr.getBounds(); size_pt = 18*mm
            drawing = Drawing(size_pt,size_pt,transform=[size_pt/(box[2]-box[0]),0,0,size_pt/(box[3]-box[1]),0,0])
            drawing.add(qr)
            renderPDF.draw(drawing,self.c,(x+(left-18)/2)*mm,(self.height-y-(h+18)/2)*mm)
        else:
            self.text(x+4,y+9,"样张" if sample else "预览",13)
            self.text(x+3,y+15,"不可扫码",9)
        cy = y+7.8
        for line in headlines:
            self.text(x+left+2.5,cy,line,size); cy+=7.6
        cy-=1.5
        for line in [*labels,*number]:
            self.text(x+left+2.5,cy,line,9.4); cy+=4
        rx = x+left+center+2
        self.text(rx,y+6,f"{card.planned_molds} 模",15)
        self.text(rx,y+12,f"第 {card.sequence}/{card.denominator} 批",11)
        self.text(rx,y+18,str(card.original_date),9.5)
        return y+h, overflow


def build_rows(card, second_inspection):
    snap = card.group.snapshot
    route = snap.get("route", {})
    std = snap.get("standard") or {}
    ss = std.get("snapshot", {})
    params = snap.get("process_parameters", {})
    criteria = applicable_criteria(ss.get("criteria", []))
    def criterion(stage):
        return next((r for r in criteria if r["stage"] == stage), None)
    def limits(row):
        return "标准待核" if not row or row.get("requirement_state") in ("RAW_UNCONFIRMED", "NOT_PROVIDED") else limit_text(row)
    def write(label, share=1):
        return Cell(label, share=share, writing=True)
    def block(label, share=1):
        return Cell(label, share=share, size=10.5, shade=True)
    def one(value, size=10.2, shade=False):
        return (Cell(value, size=size, shade=shade),)

    customers = "、".join(sorted({
        o.get("customer_code") or ("待核" if "customer_code" in o else o.get("customer") or "未登记") for o in snap["orders"]
    }))
    orders = "、".join(f"{o['order_no']}/{o['item_no'] or '-'}" for o in snap["orders"])
    yield (Cell(f"订单/项次  {orders}", 4, 10.6), Cell(f"客户 {customers}", 1, 10.6)), 6.6, False
    standard = f"检验依据 v{std['version']}" if std else "检验依据待确认"
    if std and any(c.get("requirement_state") in ("RAW_UNCONFIRMED","NOT_PROVIDED") for c in criteria):
        standard += " · 有项目待核"
    cure = params.get("primary_curing_time", {})
    main = Cell(f"{standard}     一次加硫 {parameter_text(cure)}" if usable("primary_curing_time", cure) else standard, 3)
    yield (main, write("机台员", 1)), 6.5, True

    if snap.get("sample_only") and not std:
        yield one("仅供排版核对：尚无已确认的产品标准，不用于生产。", shade=True), 7, False
    if route.get("molding"):
        yield (block("生产实测", 2), write("时间", 2), write("测量人", 1.3)), 6, True
        temp = params.get("mold_temperature_range", {})
        mode = snap.get("temperature_plan", {}).get("mode", "PENDING")
        simple = bool(temp) or (ss.get("packaging", {}).get("simple_entry") is True
                                and not any(r["stage"] in ("UPPER_TEMP", "LOWER_TEMP") for r in criteria))
        columns = []
        if simple:
            target = parameter_text(temp) if usable("mold_temperature_range",temp) else "标准待核"
            columns += [("上模实测模温",target),("下模实测模温",target)]
        elif mode != "REFERENCE" and (mode != "FIRST" or card.sequence == 1):
            for stage, label, key in (("UPPER_TEMP","接触实测·上模","upper_contact_temperature"),
                                      ("LOWER_TEMP","接触实测·下模","lower_contact_temperature")):
                p = params.get(key,{})
                if criterion(stage) or mode in ("FIRST","EVERY"):
                    columns.append((label,limits(criterion(stage)) if criterion(stage) else (
                        parameter_text(p) if usable(key,p) else "标准待核")))
        hardness = criterion("MOLDING_HARDNESS")
        if hardness:
            columns.append((hardness_label(ss, "MOLDING_HARDNESS", "成型后硬度"),limits(hardness)))
        dimensions = [r for r in criteria if r["stage"] == "DIMENSION"]
        dimension_count = max(len(dimensions), ss.get("dimension_count") or 0)
        for i in range(min(2,dimension_count)):
            row = dimensions[i] if i < len(dimensions) else None
            columns.append((short_item_label(row["name"]) if row else f"关键尺寸 {i+1}",limits(row)))
        if columns:
            for i, name in enumerate(("项目","标准","实测")):
                yield (Cell(name,.48,10,"center",i==0), *(
                    Cell(col[i] if i < 2 else " ",1,10.5,"center",i==0) for col in columns
                )), (6.2,6.3,9.5)[i], i==2
        if not simple and mode == "REFERENCE":
            ref = snap.get("temperature_plan", {}).get("reference", "")
            yield one(f"核对实际测温记录/原卡：{ref}（引用不等于已实测）",9.5), 5.6, False
        if not simple and mode == "FIRST" and card.sequence > 1:
            yield one(f"接触测温引用首卡记录 {snap.get('temperature_first_card_no','')}；换模/调机、异常按实际复测",9.5), 5.6, False
        if not simple and mode == "PENDING":
            yield one("模温时机待确认；设定模温不代替现场实测",9.5), 5.6, False
        warnings = [g for g in snap.get("guidance", []) if g.get("key") in ("primary_curing_time","pressure")
                    and g.get("warning") and not g["warning"].startswith("翔邦") and (g.get("raw_reference") or g.get("value") != "未提供")]
        if warnings:
            yield one("；".join(f"{g['label']}：{g['warning']}" for g in warnings),9.4), 5.5, False
        for row in dimensions[2:]:
            yield (Cell(f"{inspection_item_label(row['name'])}  {limits(row)}",3), write("实测",2)), 7.5, True
        if dimension_count > max(2,len(dimensions)):
            yield one(f"需测尺寸共 {dimension_count} 项，尚缺 {dimension_count-len(dimensions)} 项要求待核",9.5), 6, False

    if route.get("patrol"):
        problems = ("正常", *(v["name"] for v in snap.get("appearance",[])), "其他____")
        yield (block("外观",.65), Cell("",3.5,options=problems),write("巡检",1.2)), 7.2, True
    if snap["whole_mold"] == "REQUIRED":
        yield (block("整模检验",1.3),write("定点（孔/模）",2.5),write("整模员",1.2)), 7.2, True
    if route.get("deburr"):
        yield one(oil_instruction(params.get("oil",{})),9.8,True), 5.6, False
        yield (Cell("",1.7,options=("毛边完成","待处理")),block("实际洒油",.95),
               Cell("",1.5,options=("正常","少量","否")),write("毛边员",1.2)), 7.2, True
    if route.get("bake"):
        yield (block(f"二烤建议  {bake_text(ss.get('bake',{}))}",2.5),
               write("实测温度 °C",1.4),write("实测时间 min",1.4)), 6.8, True
        yield (write("日期",1.4),write("盘号",1.2),write("二烤员",1)), 6.8, True
    if route.get("baked_hardness"):
        title = hardness_label(ss, "BAKED_HARDNESS", "烤后硬度")
        yield (block(f"{title}  {limits(criterion('BAKED_HARDNESS'))}",2),write("实测硬度",1)), 6.8, True
        yield (write("取样盘",1),write("测量时间",1.5),write("测量人",1)), 6.8, True
    if route.get("inspection"):
        if second_inspection:
            yield (block("品检员",.7),write("一检：",1.7),write("二检：",1.7)), 7.8, True
        else:
            yield (write("品检员",1),), 7.8, True


def hardness_label(snapshot, stage, label):
    name = {"PRODUCT":"成品", "COUPON":"胶片"}.get(snapshot.get("hardness",{}).get(stage,{}).get("object"))
    return f"{label}（{name}）" if name else label


def short_item_label(item):
    return {"C/S-A":"C/S-A（轴向线径）", "C/S-R":"C/S-R（径向线径）"}.get(item,inspection_item_label(item))


def render_cards_v7(items, *, second_inspection=False, paper_layout=SINGLE):
    sizes = {(c.group.snapshot["template"].get("width_mm",190),c.group.snapshot["template"].get("height_mm",130)) for c,_ in items}
    if len(sizes) != 1:
        raise ValueError("不同纸张尺寸请分开打印。")
    width,height = sizes.pop()
    if not 180 <= width <= 220 or not 125 <= height <= 165:
        raise ValueError("纸张尺寸超出已支持的180~220 × 125~165mm。")
    out = BytesIO()
    canvas = make_canvas(out,(width*mm,height*mm),paper_layout,fuller_a4=True)
    canvas.setTitle("随货工序卡")
    paper = CardLayout(canvas,font_name(),width,height)
    footer_h = 5.3
    for card,reprint in items:
        start_card(canvas)
        snap = card.group.snapshot
        sample = snap.get("sample_only") or not card.pk or not getattr(card.pk,"int",1)
        preview = snap.get("paper_preview",False)
        y, identity_overflow = paper.header(card,sample=sample,preview=preview)
        rows = []
        for cells,minimum,stretch in build_rows(card,second_inspection):
            h = paper.height_for(cells,minimum)
            # Long statements can never run out of a fixed box or create an
            # unbounded-height row. Continue verbatim on numbered card pages.
            if h > paper.span/4:
                for cell, cell_width in paper.cell_units(cells):
                    if cell.options:
                        pieces = [tuple(label for label, _, _ in line) for line in paper.option_lines(cell,cell_width)]
                        for piece in pieces:
                            n = (Cell("",options=piece,size=cell.size),)
                            rows.append((n,paper.height_for(n,6),False))
                    else:
                        for value in paper.lines(cell.value,paper.span-6,cell.size):
                            n=(Cell(value,size=cell.size,writing=cell.writing),)
                            rows.append((n,paper.height_for(n,6),cell.writing))
            else:
                rows.append((cells,h,stretch))
        free = paper.bottom-y-footer_h-sum(h for _,h,_ in rows)
        if free < 0:
            # Fit normal full-route cards (including whole-mold + bake) without
            # sending a signature to a new page. Only reclaim padding; long
            # documents still continue rather than losing facts or shrinking type.
            fit = [paper.height_for(cells, 8.8 if cells[0].value == "实测" else 6 if stretch else 5.4)
                   for cells, _, stretch in rows]
            reclaim = sum(max(0,h-low) for (_,h,_),low in zip(rows,fit))
            if reclaim + free >= 0:
                ratio = -free/reclaim
                rows = [(cells,h-max(0,h-low)*ratio,stretch) for (cells,h,stretch),low in zip(rows,fit)]
                free = 0
        stretch_count = sum(flag for _,_,flag in rows)
        extra = min(2.4,max(0,free)/max(stretch_count,1))
        version = snap["template"].get("version")
        edition = f"版式 07 · 审定版 {version}" if isinstance(version,int) else "版式 07"
        state = "样张 · 未发号" if sample else "预览 · 不可扫码" if preview else "随货流转"
        footer = f"{state}   ·   异常同号续记   ·   {edition}" + (f"   ·   补打 {reprint} 次" if reprint else "")
        page = 1
        for cells,h,stretch in rows:
            h += extra if stretch else 0
            if y+h+footer_h > paper.bottom+.01:
                paper.row(y,(Cell(f"续下页 · {card.card_no}", size=8.7),),footer_h)
                canvas.showPage(); page+=1
                y = paper.row(paper.top,(Cell(f"随货工序卡续页 {page}  ·  {card.card_no}  ·  第 {card.sequence}/{card.denominator} 批",
                                             size=10.5,shade=True),),9)
            y = paper.row(y,cells,h)
        remaining = paper.bottom-y-footer_h
        if page == 1 and remaining >= 7:
            y = paper.row(y,(Cell("备注（选填）",writing=True),),remaining)
        paper.row(y,(Cell(footer + (" · 身份详情见续页" if identity_overflow else ""),size=8.7),),footer_h)
        if identity_overflow:
            canvas.showPage()
            y = paper.row(paper.top,(Cell(f"随货工序卡 · 身份详情 · {card.card_no}",shade=True),),9)
            for identity in identity_overflow:
                cells = (Cell(identity),)
                y = paper.row(y,cells,paper.height_for(cells,8))
        canvas.showPage(); finish_card(canvas)
    canvas.save()
    return out.getvalue()
