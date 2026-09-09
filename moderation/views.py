import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db.models import Count, Q
from django.template.loader import render_to_string
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from auditlog.utils import get_client_ip, log_action
from notifications.utils import notify
from .models import Report, UserModeration
from .serializers import (
    AdminReportActionSerializer,
    AdminReportSerializer,
    ReportCreateSerializer,
    ReportListSerializer,
    UserModerationSerializer,
)

logger = logging.getLogger(__name__)

FRONTEND_URL = getattr(settings, 'FRONTEND_URL', 'http://localhost:3000')
CURRENT_YEAR = timezone.now().year


def _send_email(template, subject, recipient, context):
    """Render an HTML email template and send it synchronously."""
    try:
        context.setdefault('frontend_url', FRONTEND_URL)
        context.setdefault('current_year', CURRENT_YEAR)
        html = render_to_string(f'emails/{template}', context)
        send_mail(
            subject=subject,
            message='',
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[recipient.email],
            html_message=html,
            fail_silently=True,
        )
    except Exception:
        pass


def _notify_admins(title, message):
    from accounts.models import User
    for admin in User.objects.filter(role='admin', is_active=True).iterator():
        notify(
            recipient=admin,
            notification_type='system',
            title=title,
            message=message,
        )


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------

class IsAdminRole(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user and
            request.user.is_authenticated and
            (request.user.is_staff or getattr(request.user, 'role', '') == 'admin')
        )


# ---------------------------------------------------------------------------
# Reporter-facing endpoints
# ---------------------------------------------------------------------------

class ReportCreateView(APIView):
    """POST /api/reports/ — any authenticated user can file a report."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = ReportCreateSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        report = serializer.save()

        _notify_admins(
            title='New Report Submitted',
            message=(
                f"New {report.get_reason_display()} report on "
                f"{report.report_type} #{report.pk}"
            ),
        )

        _send_email(
            'report_received.html',
            'We received your report — WARED',
            request.user,
            {
                'user': request.user,
                'report': report,
                'reason_display': report.get_reason_display(),
                'report_type_display': report.get_report_type_display(),
            },
        )

        log_action(
            user=request.user,
            action='create',
            model_name='Report',
            object_id=report.id,
            new_value={'report_type': report.report_type, 'reason': report.reason},
            ip_address=get_client_ip(request),
        )

        return Response(ReportListSerializer(report).data, status=status.HTTP_201_CREATED)


class MyReportsView(ListAPIView):
    """GET /api/reports/ — reporter sees their own filed reports."""
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ReportListSerializer

    def get_queryset(self):
        return (
            Report.objects
            .filter(reporter=self.request.user)
            .select_related('listing', 'reported_user')
            .order_by('-created_at')
        )


# ---------------------------------------------------------------------------
# Admin report endpoints
# ---------------------------------------------------------------------------

class AdminReportListView(ListAPIView):
    """GET /api/admin/reports/ — paginated, filterable list of all reports."""
    permission_classes = [IsAdminRole]
    serializer_class = AdminReportSerializer

    def get_queryset(self):
        qs = Report.objects.select_related(
            'reporter', 'listing', 'reported_user', 'resolved_by'
        )
        params = self.request.query_params

        if s := params.get('status'):
            qs = qs.filter(status=s)
        if p := params.get('priority'):
            qs = qs.filter(priority=p)
        if rt := params.get('report_type'):
            qs = qs.filter(report_type=rt)
        if r := params.get('reason'):
            qs = qs.filter(reason=r)

        sort = params.get('ordering', '-created_at')
        if sort not in ('priority', '-priority', 'created_at', '-created_at'):
            sort = '-created_at'
        return qs.order_by(sort)

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        response.data['pending_count'] = Report.objects.filter(status='pending').count()
        return response

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx['request'] = self.request
        return ctx


class AdminReportDetailView(APIView):
    """GET /api/admin/reports/{id}/ — full detail."""
    permission_classes = [IsAdminRole]

    def get(self, request, pk):
        try:
            report = Report.objects.select_related(
                'reporter', 'listing', 'reported_user',
                'reported_showroom', 'reported_workshop', 'resolved_by',
            ).get(pk=pk)
        except Report.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(AdminReportSerializer(report, context={'request': request}).data)


class AdminReportActionView(APIView):
    """PATCH /api/admin/reports/{id}/ — update status, take action."""
    permission_classes = [IsAdminRole]

    def patch(self, request, pk):
        try:
            report = Report.objects.select_related('listing', 'reported_user').get(pk=pk)
        except Report.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        serializer = AdminReportActionSerializer(
            data=request.data,
            context={'report': report, 'request': request},
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        old_status   = report.status
        new_status   = data.get('status', report.status)
        action_taken = data.get('action_taken', report.action_taken)

        if 'admin_notes' in data:
            report.admin_notes = data['admin_notes']
        report.status       = new_status
        report.action_taken = action_taken

        if new_status in ('resolved', 'dismissed') and old_status not in ('resolved', 'dismissed'):
            report.resolved_by = request.user
            report.resolved_at = timezone.now()

        report.save()

        self._execute_action(request, report, action_taken, data)

        # Notify reporter
        notify(
            recipient=report.reporter,
            notification_type='system',
            title='Your report has been reviewed',
            message=(
                f"Your {report.get_report_type_display()} report "
                f"has been {report.get_status_display().lower()}."
            ),
        )
        _send_email(
            'report_resolved.html',
            'Your report has been reviewed — WARED',
            report.reporter,
            {
                'user': report.reporter,
                'report': report,
                'action_taken_display': report.get_action_taken_display(),
                'status_display': report.get_status_display(),
            },
        )

        log_action(
            user=request.user,
            action='status_change',
            model_name='Report',
            object_id=report.id,
            old_value={'status': old_status},
            new_value={'status': new_status, 'action_taken': action_taken},
            ip_address=get_client_ip(request),
        )

        return Response(AdminReportSerializer(report, context={'request': request}).data)

    def _execute_action(self, request, report, action_taken, data):
        listing     = report.listing
        target_user = report.reported_user or (
            listing.owner if listing and hasattr(listing, 'owner') else None
        )

        if action_taken == 'listing_removed' and listing:
            listing.is_active = False
            listing.save(update_fields=['is_active'])

        elif action_taken == 'listing_suspended' and listing:
            listing.status = 'rejected'
            listing.rejection_reason = 'Suspended due to a moderation report.'
            listing.save(update_fields=['status', 'rejection_reason'])

        elif action_taken == 'user_warned' and target_user:
            target_user.warning_count = (target_user.warning_count or 0) + 1
            target_user.save(update_fields=['warning_count'])
            UserModeration.objects.create(
                user=target_user,
                action='warning',
                reason=report.admin_notes or 'Warning issued after report review.',
                related_report=report,
                issued_by=request.user,
            )
            notify(
                recipient=target_user,
                notification_type='system',
                title='You have received a warning',
                message='A warning has been issued on your account.',
            )
            _send_email(
                'user_warning.html',
                'Account Warning — WARED',
                target_user,
                {
                    'user': target_user,
                    'reason': report.admin_notes or 'Violation of community guidelines.',
                    'warning_count': target_user.warning_count,
                },
            )

        elif action_taken == 'user_suspended' and target_user:
            suspended_until = data.get('suspended_until') or (timezone.now() + timedelta(days=7))
            reason = report.admin_notes or 'Temporary suspension following report.'
            target_user.is_suspended     = True
            target_user.suspended_until  = suspended_until
            target_user.suspension_reason = reason
            target_user.save(
                update_fields=['is_suspended', 'suspended_until', 'suspension_reason']
            )
            UserModeration.objects.create(
                user=target_user,
                action='suspension',
                reason=reason,
                related_report=report,
                issued_by=request.user,
                expires_at=suspended_until,
            )
            notify(
                recipient=target_user,
                notification_type='system',
                title='Your account has been suspended',
                message=f"Suspension active until {suspended_until.strftime('%Y-%m-%d')}.",
            )
            _send_email(
                'user_suspended.html',
                'Account Suspended — WARED',
                target_user,
                {
                    'user': target_user,
                    'reason': reason,
                    'suspended_until': suspended_until,
                },
            )

        elif action_taken == 'user_banned' and target_user:
            reason = report.admin_notes or 'Permanent ban following report.'
            target_user.is_banned  = True
            target_user.ban_reason = reason
            target_user.save(update_fields=['is_banned', 'ban_reason'])
            # Deactivate all listings
            target_user.listings.filter(is_active=True).update(is_active=False)
            UserModeration.objects.create(
                user=target_user,
                action='ban',
                reason=reason,
                related_report=report,
                issued_by=request.user,
            )
            notify(
                recipient=target_user,
                notification_type='system',
                title='Your account has been banned',
                message='Your account has been permanently banned.',
            )
            _send_email(
                'user_banned.html',
                'Account Banned — WARED',
                target_user,
                {'user': target_user, 'reason': reason},
            )


class AdminReportStatsView(APIView):
    """GET /api/admin/reports/stats/ — aggregate stats."""
    permission_classes = [IsAdminRole]

    def get(self, request):
        now         = timezone.now()
        week_start  = now - timedelta(days=7)
        month_start = now - timedelta(days=30)

        by_status = dict(
            Report.objects.values('status')
            .annotate(c=Count('id'))
            .values_list('status', 'c')
        )
        by_type = dict(
            Report.objects.values('report_type')
            .annotate(c=Count('id'))
            .values_list('report_type', 'c')
        )
        top_reasons = list(
            Report.objects.values('reason')
            .annotate(count=Count('id'))
            .order_by('-count')[:10]
        )

        return Response({
            'total':               Report.objects.count(),
            'by_status':           by_status,
            'by_type':             by_type,
            'top_reasons':         top_reasons,
            'this_week':           Report.objects.filter(created_at__gte=week_start).count(),
            'this_month':          Report.objects.filter(created_at__gte=month_start).count(),
            'pending_count':       by_status.get('pending', 0),
            'investigating_count': by_status.get('investigating', 0),
        })


# ---------------------------------------------------------------------------
# Admin UserModeration endpoints
# ---------------------------------------------------------------------------

class UserModerationListCreateView(APIView):
    """
    GET  /api/admin/moderation/users/ — list all moderation actions
    POST /api/admin/moderation/users/ — manually warn/suspend/ban a user
    """
    permission_classes = [IsAdminRole]

    def get(self, request):
        qs = UserModeration.objects.select_related('user', 'issued_by', 'related_report')
        if uid := request.query_params.get('user_id'):
            qs = qs.filter(user_id=uid)
        if action := request.query_params.get('action'):
            qs = qs.filter(action=action)
        return Response(UserModerationSerializer(qs, many=True).data)

    def post(self, request):
        serializer = UserModerationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d      = serializer.validated_data
        action = d['action']
        user   = d['user']
        reason = d['reason']

        mod = UserModeration.objects.create(
            user=user,
            action=action,
            reason=reason,
            related_report=d.get('related_report'),
            issued_by=request.user,
            expires_at=d.get('expires_at'),
        )

        if action == 'warning':
            user.warning_count = (user.warning_count or 0) + 1
            user.save(update_fields=['warning_count'])
            notify(
                recipient=user, notification_type='system',
                title='You have received a warning', message=reason,
            )
            _send_email(
                'user_warning.html', 'Account Warning — WARED', user,
                {'user': user, 'reason': reason, 'warning_count': user.warning_count},
            )

        elif action == 'suspension':
            user.is_suspended     = True
            user.suspended_until  = d['expires_at']
            user.suspension_reason = reason
            user.save(update_fields=['is_suspended', 'suspended_until', 'suspension_reason'])
            notify(
                recipient=user, notification_type='system',
                title='Your account has been suspended', message=reason,
            )
            _send_email(
                'user_suspended.html', 'Account Suspended — WARED', user,
                {'user': user, 'reason': reason, 'suspended_until': d['expires_at']},
            )

        elif action == 'ban':
            user.is_banned  = True
            user.ban_reason = reason
            user.save(update_fields=['is_banned', 'ban_reason'])
            user.listings.filter(is_active=True).update(is_active=False)
            notify(
                recipient=user, notification_type='system',
                title='Your account has been banned', message=reason,
            )
            _send_email(
                'user_banned.html', 'Account Banned — WARED', user,
                {'user': user, 'reason': reason},
            )

        log_action(
            user=request.user,
            action='create',
            model_name='UserModeration',
            object_id=mod.id,
            new_value={'action': action, 'target_user': user.id},
            ip_address=get_client_ip(request),
        )

        return Response(UserModerationSerializer(mod).data, status=status.HTTP_201_CREATED)


class UserModerationDetailView(APIView):
    """PATCH /api/admin/moderation/users/{id}/ — lift a restriction."""
    permission_classes = [IsAdminRole]

    def patch(self, request, pk):
        try:
            mod = UserModeration.objects.select_related('user').get(pk=pk)
        except UserModeration.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        lift_reason = request.data.get('reason', 'Restriction lifted by admin.')

        mod.is_active = False
        mod.save(update_fields=['is_active'])

        lifted = UserModeration.objects.create(
            user=mod.user,
            action='lifted',
            reason=lift_reason,
            issued_by=request.user,
        )

        user = mod.user
        if mod.action == 'suspension':
            user.is_suspended    = False
            user.suspended_until = None
            user.save(update_fields=['is_suspended', 'suspended_until'])
        elif mod.action == 'ban':
            user.is_banned  = False
            user.ban_reason = ''
            user.save(update_fields=['is_banned', 'ban_reason'])

        notify(
            recipient=user,
            notification_type='system',
            title='Account restriction lifted',
            message='Your account restriction has been lifted by an admin.',
        )

        log_action(
            user=request.user,
            action='update',
            model_name='UserModeration',
            object_id=mod.id,
            new_value={'is_active': False, 'lifted': True},
            ip_address=get_client_ip(request),
        )

        return Response(UserModerationSerializer(lifted).data)
