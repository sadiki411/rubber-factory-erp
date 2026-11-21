"""Physical A4 imposition; renderers keep drawing each original-size logical card."""
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas
from rest_framework.exceptions import ValidationError

A4_TWO_UP = "A4_TWO_UP"
SINGLE = "SINGLE"


def print_layout(value):
    if value not in (A4_TWO_UP, SINGLE):
        raise ValidationError({"paper_layout": "请选择 A4 双卡打印或原尺寸单卡。"})
    return value


class TwoUpCanvas(Canvas):
    """A logical page is captured as a vector form; no rasterizing or QR regeneration."""

    def __init__(self, target, *, pagesize, **kwargs):
        self.card_size = pagesize
        self.primaries = []
        self.continuations = []
        self.current_card = []
        self.form_count = 0
        self.capturing = False
        super().__init__(target, pagesize=A4, **kwargs)

    def start_card(self):
        self._start_logical_page()

    def _start_logical_page(self):
        self.form_count += 1
        self.form_name = f"LogicalCardPage{self.form_count}"
        self.beginForm(self.form_name, 0, 0, *self.card_size)
        self.capturing = True

    def showPage(self):
        if not self.capturing:
            return super().showPage()
        self.endForm()
        self.current_card.append(self.form_name)
        self._start_logical_page()

    def finish_card(self):
        # The renderers call showPage themselves for all nonempty logical pages.
        if self._code or not self.current_card:
            raise ValueError("工序卡没有完成逻辑页，无法安全拼版。")
        self.endForm()
        self.capturing = False
        self.primaries.append(self.current_card[0])
        self.continuations.extend(self.current_card[1:])
        self.current_card = []

    def _sheet(self, names, *, cut):
        width, height = A4
        card_width, card_height = self.card_size
        half = height / 2
        # Keep normal 190 x 130 mm cards at 100%; fit exceptional sizes in a
        # half sheet with printer margins, never stretch or rotate the QR.
        scale = min(1, (width - 14*mm) / card_width, (half - 14*mm) / card_height)
        for slot, name in enumerate(names):
            self.saveState()
            self.translate((width-card_width*scale)/2, (1-slot)*half+(half-card_height*scale)/2)
            self.scale(scale, scale)
            self.doForm(name)
            self.restoreState()
        if cut:
            self.setLineWidth(.35)
            self.setStrokeGray(.55)
            self.line(7*mm, half, width-7*mm, half)
        super().showPage()

    def save(self):
        for i in range(0, len(self.primaries), 2):
            self._sheet(self.primaries[i:i+2], cut=True)
        # Existing long cards may have continuation pages. Keep them after
        # the primary pairs, with their original IDs; never fill an odd slot.
        for i in range(0, len(self.continuations), 2):
            self._sheet(self.continuations[i:i+2], cut=True)
        super().save()


def make_canvas(target, pagesize, paper_layout):
    cls = TwoUpCanvas if paper_layout == A4_TWO_UP else Canvas
    return cls(target, pagesize=pagesize, pageCompression=1, invariant=1)


def start_card(canvas):
    if isinstance(canvas, TwoUpCanvas):
        canvas.start_card()


def finish_card(canvas):
    if isinstance(canvas, TwoUpCanvas):
        canvas.finish_card()
