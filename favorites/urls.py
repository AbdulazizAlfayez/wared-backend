from django.urls import path
from . import views

urlpatterns = [
    path('', views.favorites_list, name='favorites-list'),
    path('count/', views.favorites_count, name='favorites-count'),
    path('<int:pk>/', views.favorite_delete, name='favorite-delete'),
]
