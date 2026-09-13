from django.urls import path

from . import views

urlpatterns = [
    path(
        'listings/<int:listing_id>/like/',
        views.toggle_like,
        name='listing-like',
    ),
    path(
        'listings/<int:listing_id>/comments/',
        views.ListingCommentListCreateView.as_view(),
        name='listing-comments',
    ),
    path(
        'comments/<int:pk>/',
        views.delete_comment,
        name='comment-delete',
    ),
    path(
        'comments/<int:pk>/report/',
        views.report_comment,
        name='comment-report',
    ),
]
