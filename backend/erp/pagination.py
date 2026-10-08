from rest_framework.pagination import PageNumberPagination


class PublicLocationHistoryPagination(PageNumberPagination):
    """Bound anonymous label lookups, independently of ERP selector page sizes."""

    page_size = 20
