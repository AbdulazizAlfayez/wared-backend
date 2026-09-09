from rest_framework.views import exception_handler


def bilingual_exception_handler(exc, context):
    """
    Custom DRF exception handler that adds a 'language' key to all error
    responses so the frontend knows which locale the message was generated in.
    Falls through to the standard DRF exception_handler for all logic.
    """
    response = exception_handler(exc, context)

    if response is not None:
        request = context.get('request')
        lang = 'en'
        if request:
            accept = request.headers.get('Accept-Language', 'en')
            lang = accept[:2].lower()
            if lang not in ('en', 'ar'):
                lang = 'en'
        if isinstance(response.data, dict):
            response.data['language'] = lang

    return response
