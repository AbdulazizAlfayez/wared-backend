from django.urls import path

from . import views

urlpatterns = [
    path('chat/', views.ChatView.as_view(), name='assistant-chat'),
    path('conversations/', views.ConversationListView.as_view(), name='assistant-conversations'),
    path('conversations/<int:pk>/', views.ConversationDetailView.as_view(), name='assistant-conversation-detail'),
]
