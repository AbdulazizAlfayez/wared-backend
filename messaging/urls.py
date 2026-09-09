from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import BlockedUserViewSet, ConversationViewSet

router = DefaultRouter()
router.register(r'conversations',  ConversationViewSet,  basename='conversation')
router.register(r'blocked-users',  BlockedUserViewSet,   basename='blocked-user')

urlpatterns = [
    path('', include(router.urls)),
]
