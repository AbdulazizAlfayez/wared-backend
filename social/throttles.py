from rest_framework.throttling import UserRateThrottle


class CommentRateThrottle(UserRateThrottle):
    """10/min — rates live in settings.DEFAULT_THROTTLE_RATES."""

    scope = 'social_comment'


class LikeRateThrottle(UserRateThrottle):
    """60/min. Likes are a tap, so the ceiling is deliberately generous."""

    scope = 'social_like'


class ReportRateThrottle(UserRateThrottle):
    """20/hour. Reporting is rare and abusable, so it is the tightest."""

    scope = 'social_report'
