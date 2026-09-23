"""
The one endpoint every inspector is served through.

`GET /api/admin/inspect/<entity>/<id>/`. Admin only, with no partial answer
for anyone else: an importer who guesses the URL of their own car still gets
403, because the point of this view is that it holds things no importer may
read. And because an admin reading a record is itself worth knowing, every
successful read writes an audit row.
"""
from django.core.exceptions import ObjectDoesNotExist
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from auditlog.utils import get_client_ip, get_user_agent, log_action
from cars.permissions import IsAdminRole

from . import listings  # noqa: F401  — registers the listing inspector
from .base import REGISTRY


class InspectView(APIView):
    """One entity, everything known about it."""

    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request, entity, pk):
        inspector_cls = REGISTRY.get(entity)
        if inspector_cls is None:
            return Response(
                {'detail': f"No inspector for '{entity}'.",
                 'available': sorted(REGISTRY)},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            instance = inspector_cls.get_object(pk)
        except ObjectDoesNotExist:
            return Response(
                {'detail': f'No {inspector_cls.label.lower()} with id {pk}.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        payload = inspector_cls(instance, request).build()
        self._record_the_read(request, inspector_cls, instance)
        return Response(payload)

    def _record_the_read(self, request, inspector_cls, instance):
        """
        Who opened this record, when, from where.

        Written after the payload is assembled so a failed read does not leave
        a row claiming it happened. The trail the inspector displays therefore
        includes its own views — deliberately: "three admins looked at this
        car last night" is exactly the kind of thing an audit is for.
        """
        log_action(
            user=request.user,
            action='view',
            model_name=inspector_cls.model_name,
            object_id=instance.pk,
            new_value={'inspected': inspector_cls.entity},
            ip_address=get_client_ip(request),
            user_agent=get_user_agent(request),
        )
