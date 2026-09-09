from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema

import logging

from auditlog.utils import get_client_ip, log_action
from .models import ImporterApplication
from .serializers import ImporterApplicationAdminSerializer, ImporterApplicationCreateSerializer

_logger = logging.getLogger(__name__)


def _notify_admins_new_application(application):
    """Gap #12: Email all admins when a new importer application is submitted."""
    try:
        from django.contrib.auth import get_user_model
        from notifications.emails import send_templated_email
        User = get_user_model()
        admins = User.objects.filter(role='admin', is_active=True).values_list('email', flat=True)
        applicant = application.applicant
        for admin_email in admins:
            try:
                send_templated_email(
                    to_email=admin_email,
                    subject=f'طلب مستورد جديد · {application.business_name}',
                    template_name='new_application_admin',
                    context={
                        'business_name': application.business_name,
                        'business_type': application.business_type,
                        'city': application.city or '—',
                        'applicant_name': applicant.name or applicant.email,
                        'applicant_email': applicant.email,
                    },
                )
            except Exception as exc:
                _logger.error("Admin application notification to %s failed: %s", admin_email, exc)
    except Exception as exc:
        _logger.error("_notify_admins_new_application failed: %s", exc)


@extend_schema(tags=['Importer Applications'])
class ImporterApplicationCreateView(generics.CreateAPIView):
    """
    POST /api/importer-applications/

    Authenticated users submit an importer application.
    - Users who are already importer/admin receive a 400 informing them they
      already have access (the frontend guards this too, but belt-and-suspenders).
    - Duplicate pending applications are rejected to prevent spam.
    """

    serializer_class   = ImporterApplicationCreateSerializer
    permission_classes = [IsAuthenticated]

    def create(self, request, *args, **kwargs):
        user = request.user

        # Already an importer or admin — no need to apply.
        if getattr(user, 'role', None) in ('importer', 'admin'):
            return Response(
                {'detail': 'Your account already has importer access.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Prevent duplicate pending applications.
        if ImporterApplication.objects.filter(applicant=user, status='pending').exists():
            return Response(
                {'detail': 'You already have a pending importer application under review.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        application = serializer.save(applicant=user)

        log_action(
            user=user,
            action='create',
            model_name='ImporterApplication',
            object_id=application.pk,
            ip_address=get_client_ip(request),
        )

        # Notify admins about the new application
        _notify_admins_new_application(application)

        return Response(serializer.data, status=status.HTTP_201_CREATED)


@extend_schema(tags=['Importer Applications'])
class ImporterApplicationAdminListView(generics.ListAPIView):
    """
    GET /api/importer-applications/admin/

    Admin-only list of all applications, filterable by ?status=pending|approved|rejected.
    """

    serializer_class   = ImporterApplicationAdminSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if getattr(user, 'role', None) != 'admin':
            return ImporterApplication.objects.none()
        qs = ImporterApplication.objects.select_related('applicant').all()
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)
        return qs


@extend_schema(tags=['Importer Applications'])
class ImporterApplicationAdminDetailView(generics.RetrieveUpdateAPIView):
    """
    GET  /api/importer-applications/admin/{id}/
    PATCH /api/importer-applications/admin/{id}/

    Admin reviews and approves / rejects an application.
    On approval, the applicant's role is set to 'importer'.
    """

    serializer_class   = ImporterApplicationAdminSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if getattr(user, 'role', None) != 'admin':
            return ImporterApplication.objects.none()
        return ImporterApplication.objects.select_related('applicant').all()

    def perform_update(self, serializer):
        application = serializer.save()
        # When approved, promote the applicant to importer.
        if application.status == 'approved':
            applicant = application.applicant
            if applicant.role != 'importer':
                applicant.role = 'importer'
                applicant.save(update_fields=['role'])
                # DEPRECATED: No subscriptions in commission-only model
                # try:
                #     from datetime import timedelta
                #     from django.utils import timezone
                #     from subscriptions.models import DealerSubscription, SubscriptionPlan
                #     if not DealerSubscription.objects.filter(dealer=applicant).exists():
                #         free_plan = SubscriptionPlan.objects.get(slug='free')
                #         DealerSubscription.objects.create(
                #             dealer=applicant,
                #             plan=free_plan,
                #             billing_cycle='monthly',
                #             status='active',
                #             expires_at=timezone.now() + timedelta(days=3650),
                #         )
                # except Exception:
                #     pass
                log_action(
                    user=self.request.user,
                    action='update',
                    model_name='User',
                    object_id=applicant.pk,
                    new_value={'role': 'importer'},
                    ip_address=get_client_ip(self.request),
                )
        log_action(
            user=self.request.user,
            action='update',
            model_name='ImporterApplication',
            object_id=application.pk,
            new_value={'status': application.status},
            ip_address=get_client_ip(self.request),
        )

        # Notify applicant of decision
        applicant = application.applicant
        try:
            from notifications.utils import notify
            if application.status == 'approved':
                notify(
                    recipient=applicant,
                    notification_type='application_approved',
                    title='تم قبول طلبك / Your application has been approved',
                    message='مبروك! تقدر الحين تعرض سياراتك على وارد.',
                )
                try:
                    from notifications.emails import send_templated_email
                    send_templated_email(
                        to_email=applicant.email,
                        subject='تم قبول طلبك كمستورد / Your importer application is approved — WARED',
                        template_name='listing_approved',
                        context={
                            'name': applicant.name or applicant.email,
                            'make': 'Importer',
                            'model': 'Application',
                            'year': '',
                            'price': '',
                            'listing_id': '',
                        },
                    )
                except Exception as exc:
                    import logging
                    logging.getLogger(__name__).error("Application approved email failed: %s", exc)

            elif application.status == 'rejected':
                reason = getattr(application, 'rejection_reason', '') or ''
                notify(
                    recipient=applicant,
                    notification_type='application_rejected',
                    title='تم رفض طلبك / Your application was rejected',
                    message=f'السبب: {reason}' if reason else 'تواصل معنا للمزيد من التفاصيل.',
                    metadata={'rejection_reason': reason},
                )
                try:
                    from notifications.emails import send_templated_email
                    send_templated_email(
                        to_email=applicant.email,
                        subject='تحديث على طلبك / Application update — WARED',
                        template_name='listing_rejected',
                        context={
                            'name': applicant.name or applicant.email,
                            'make': 'Importer',
                            'model': 'Application',
                            'year': '',
                            'listing_id': '',
                            'rejection_reason': reason,
                        },
                    )
                except Exception as exc:
                    import logging
                    logging.getLogger(__name__).error("Application rejected email failed: %s", exc)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error("Application notification failed: %s", exc)


# Backwards-compatible aliases (in case anything still imports old names)
DealerApplicationCreateView = ImporterApplicationCreateView
DealerApplicationAdminListView = ImporterApplicationAdminListView
DealerApplicationAdminDetailView = ImporterApplicationAdminDetailView
