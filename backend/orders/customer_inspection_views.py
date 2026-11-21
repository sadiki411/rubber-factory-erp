from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import ValidationError

from quality.models import QualityOrder
from flowcards import preparation, services
from flowcards.views import ErrorsMixin
from . import customer_inspection as ci
from .models import ProductSpecification


def printable_response(payloads, filename):
    from .customer_inspection_pdf import render
    result = HttpResponse(render(payloads), content_type="application/pdf")
    result["Content-Disposition"] = f'attachment; filename="{filename}"'
    result["Cache-Control"] = "private, no-store"
    return result


class OrderCustomerInspectionView(ErrorsMixin, APIView):
    def order(self, pk):
        return get_object_or_404(QualityOrder.objects.select_related("source_batch", "last_source_batch").defer("source_batch__payload", "last_source_batch__payload"), pk=pk)

    def get(self, request, pk):
        order = self.order(pk)
        data = ci.order_payload(order)
        if data is not None:
            data["can_review"] = preparation.can_maintain(request.user)
            data["available"] = ci.current_options(order) if data["status"] == "MISSING" else []
            data["changes"] = ci.previous_diff(order, data)
        return Response({"inspection": data})

    def post(self, request, pk):
        preparation.require_maintainer(request.user)
        return Response({"inspection": ci.review_order(self.order(pk), request.user, request.data)})


class OrderCustomerInspectionPrintView(OrderCustomerInspectionView):
    http_method_names = ["get", "head", "options"]

    def get(self, request, pk):
        data = ci.order_payload(self.order(pk))
        if not data or not data["groups"]:
            raise Http404("本订单无可打印的已关联标准。")
        return printable_response([data], f"order-{pk}-customer-inspection.pdf")


class ProductCustomerInspectionView(ErrorsMixin, APIView):
    def get(self, request, pk):
        product = get_object_or_404(ProductSpecification, pk=pk)
        page = services.integer(request.query_params.get("page", 1), "page", maximum=100000)
        rows, has_next = ci.product_reviews(product, page)
        return Response({"results": rows, "has_next": has_next})
