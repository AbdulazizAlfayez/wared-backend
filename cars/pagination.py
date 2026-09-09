"""
Opt-in cursor pagination for listing endpoints.

Usage: append ?pagination=cursor to any listing list request.
Without it, the default PageNumberPagination is used (web frontend compat).
"""
from rest_framework.pagination import CursorPagination, PageNumberPagination


class ListingCursorPagination(CursorPagination):
    """Stable cursor pagination ordered by -created_at, -id."""
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 50
    ordering = ('-created_at', '-id')


class OptInCursorPagination(PageNumberPagination):
    """
    Default: page-number pagination (web compat).
    With ?pagination=cursor: switches to cursor pagination (mobile).
    """
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 50

    _cursor_instance = None

    def paginate_queryset(self, queryset, request, view=None):
        if request.query_params.get('pagination') == 'cursor':
            self._cursor_instance = ListingCursorPagination()
            return self._cursor_instance.paginate_queryset(queryset, request, view)
        return super().paginate_queryset(queryset, request, view)

    def get_paginated_response(self, data):
        if self._cursor_instance:
            return self._cursor_instance.get_paginated_response(data)
        return super().get_paginated_response(data)

    def get_paginated_response_schema(self, schema):
        # For Swagger — show the page-number schema as default
        return super().get_paginated_response_schema(schema)
