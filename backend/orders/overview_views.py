from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView
from .overview import ReadQuery, build_workbench, build_order_overview


class WorkbenchView(APIView):
    @extend_schema(parameters=[ReadQuery], responses=dict)
    def get(self, request):
        query = ReadQuery(data=request.query_params)
        query.is_valid(raise_exception=True)
        return Response(build_workbench(**query.validated_data))


class OrderOverviewView(APIView):
    @extend_schema(parameters=[ReadQuery], responses=dict)
    def get(self, request, pk):
        query = ReadQuery(data=request.query_params)
        query.is_valid(raise_exception=True)
        return Response(build_order_overview(pk, **query.validated_data))
