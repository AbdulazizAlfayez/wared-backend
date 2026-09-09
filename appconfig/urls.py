from django.urls import path
from .views import ConfigView

urlpatterns = [
    path('config/', ConfigView.as_view(), name='app-config'),
]
