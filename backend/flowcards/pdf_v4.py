"""New draft renderer: keep v3 original output readable, fix applicable columns."""
from .pdf_v3 import render_cards_v3


def render_cards_v4(items):
    return render_cards_v3(items, applicable_dimensions=True)
