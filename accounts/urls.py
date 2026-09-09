from django.urls import path

from .mobile_views import (
    MobileLoginView,
    MobileLogoutView,
    MobileRefreshView,
    MobileRegisterView,
)
from .views import (
    AdminUserListView,
    AdminUserUpdateView,
    AdminVerificationActionView,
    AdminVerificationListView,
    CSRFTokenView,
    ChangePasswordView,
    GoogleLoginView,
    LoginView,
    LogoutView,
    MeView,
    MyVerificationRequestsView,
    MyVerificationStatusView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    ProfileUpdateView,
    PublicProfileView,
    RegisterView,
    ResendVerificationOTP,
    SendEmailVerificationOTP,
    SendLoginOTP,
    SendPhoneVerificationOTP,
    SubmitVerificationView,
    TokenRefreshCookieView,
    UserRoleView,
    VerifyEmailOTP,
    VerifyLoginOTP,
    VerifyPhoneOTP,
    AccountDeletionCancelView,
    AccountDeletionRequestView,
    AccountDeletionStatusView,
    DataExportRequestView,
    DataExportStatusView,
)

urlpatterns = [
    # Auth endpoints
    path('auth/register/', RegisterView.as_view(), name='register'),
    path('auth/login/', LoginView.as_view(), name='login'),
    path('auth/google/', GoogleLoginView.as_view(), name='google-login'),
    path('auth/logout/', LogoutView.as_view(), name='logout'),
    path('auth/token/refresh/', TokenRefreshCookieView.as_view(), name='token_refresh'),
    path('auth/csrf/', CSRFTokenView.as_view(), name='csrf'),

    # Mobile auth (token-based, no cookies, no CSRF)
    path('auth/mobile/login/',    MobileLoginView.as_view(),    name='mobile-login'),
    path('auth/mobile/register/', MobileRegisterView.as_view(), name='mobile-register'),
    path('auth/mobile/refresh/',  MobileRefreshView.as_view(),  name='mobile-refresh'),
    path('auth/mobile/logout/',   MobileLogoutView.as_view(),   name='mobile-logout'),

    # Password reset
    path('auth/password-reset/', PasswordResetRequestView.as_view(), name='password-reset-request'),
    path('auth/password-reset/confirm/', PasswordResetConfirmView.as_view(), name='password-reset-confirm'),
    path('auth/change-password/', ChangePasswordView.as_view(), name='change-password'),

    # OTP endpoints (Phase 3.4)
    path('auth/otp/send-verification/', SendPhoneVerificationOTP.as_view(), name='otp-send-verification'),
    path('auth/otp/verify-phone/',      VerifyPhoneOTP.as_view(),            name='otp-verify-phone'),
    path('auth/otp/send-login/',        SendLoginOTP.as_view(),              name='otp-send-login'),
    path('auth/otp/verify-login/',      VerifyLoginOTP.as_view(),            name='otp-verify-login'),

    # Email OTP endpoints (Phase 3.5)
    path('auth/otp/send-email-verification/', SendEmailVerificationOTP.as_view(), name='otp-send-email-verification'),
    path('auth/otp/verify-email/',            VerifyEmailOTP.as_view(),            name='otp-verify-email'),
    path('auth/otp/resend/',                  ResendVerificationOTP.as_view(),     name='otp-resend'),

    # User profile & management
    path('me/', MeView.as_view(), name='me'),
    # /api/users/me/profile/ must come BEFORE /api/users/<int:pk>/... to avoid 'me' matching as int
    path('users/me/profile/', ProfileUpdateView.as_view(), name='profile-update'),
    path('users/<int:pk>/profile/', PublicProfileView.as_view(), name='profile-public'),
    path('users/<int:pk>/role', UserRoleView.as_view(), name='user-role'),
    path('users/<int:pk>/', AdminUserUpdateView.as_view(), name='user-update'),
    # Admin user list
    path('users/', AdminUserListView.as_view(), name='user-list'),

    # Verification endpoints (Phase 5.1)
    path('verification/submit/', SubmitVerificationView.as_view(), name='verification-submit'),
    path('verification/status/', MyVerificationStatusView.as_view(), name='verification-status'),
    path('verification/requests/', MyVerificationRequestsView.as_view(), name='verification-requests'),
    path('admin/verifications/', AdminVerificationListView.as_view(), name='admin-verifications'),
    path('admin/verifications/<int:pk>/', AdminVerificationActionView.as_view(), name='admin-verification-action'),

    # Phase 5.5 — Account Deletion
    path('account/deletion/request/', AccountDeletionRequestView.as_view(), name='account-deletion-request'),
    path('account/deletion/cancel/', AccountDeletionCancelView.as_view(), name='account-deletion-cancel'),
    path('account/deletion/status/', AccountDeletionStatusView.as_view(), name='account-deletion-status'),

    # Phase 5.5 — Data Export
    path('account/export/', DataExportRequestView.as_view(), name='data-export-request'),
    path('account/export/status/', DataExportStatusView.as_view(), name='data-export-status'),
]
