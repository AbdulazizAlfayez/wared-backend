"""
Bilingual error / UI messages for the cars (and related) apps.
Usage:
    from cars.messages import get_message
    msg = get_message('not_found', lang='ar')   # → 'غير موجود.'
"""

MESSAGES = {
    'not_found': {
        'en': 'Not found.',
        'ar': 'غير موجود.',
    },
    'permission_denied': {
        'en': 'Permission denied.',
        'ar': 'تم رفض الإذن.',
    },
    'authentication_required': {
        'en': 'Authentication required.',
        'ar': 'يجب تسجيل الدخول.',
    },
    'invalid_status_transition': {
        'en': 'Invalid status transition.',
        'ar': 'انتقال حالة غير صالح.',
    },
    'listing_not_approved': {
        'en': 'Listing is not approved.',
        'ar': 'الإعلان غير معتمد.',
    },
    'max_images_reached': {
        'en': 'Maximum 20 images per listing.',
        'ar': 'الحد الأقصى 20 صورة لكل إعلان.',
    },
    'cannot_book_own': {
        'en': 'You cannot book your own listing.',
        'ar': 'لا يمكنك حجز إعلانك الخاص.',
    },
    'past_date': {
        'en': 'Date cannot be in the past.',
        'ar': 'لا يمكن اختيار تاريخ في الماضي.',
    },
    'max_leads': {
        'en': 'Maximum leads reached for this listing.',
        'ar': 'تم الوصول للحد الأقصى من الاستفسارات لهذا الإعلان.',
    },
    'weak_password': {
        'en': 'Password is too weak.',
        'ar': 'كلمة المرور ضعيفة جداً.',
    },
    'login_failed': {
        'en': 'Invalid credentials.',
        'ar': 'بيانات الدخول غير صحيحة.',
    },
    'phone_invalid': {
        'en': 'Invalid Saudi phone number format.',
        'ar': 'صيغة رقم الهاتف السعودي غير صحيحة.',
    },
}


def get_message(key: str, lang: str = 'en') -> str:
    """
    Return the message for *key* in *lang*.
    Falls back to English if the language is not available,
    and to the key itself if the key is unknown.
    """
    msg = MESSAGES.get(key, {})
    return msg.get(lang) or msg.get('en') or key
