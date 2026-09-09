from django.urls import path
from . import views

urlpatterns = [
    path('reviews/',                                  views.ReviewListCreateView.as_view(),  name='review-list-create'),
    path('reviews/mine/',                             views.MyReviewsView.as_view(),         name='review-mine'),
    path('reviews/about-me/',                         views.ReviewsAboutMeView.as_view(),    name='review-about-me'),
    path('reviews/<int:pk>/',                         views.ReviewDetailView.as_view(),      name='review-detail'),
    path('reviews/<int:pk>/reply/',                   views.ReviewReplyView.as_view(),       name='review-reply'),
    path('users/<int:user_id>/reviews/',              views.UserReviewsView.as_view(),       name='user-reviews'),
    path('showrooms/<int:showroom_id>/reviews/',      views.ShowroomReviewsView.as_view(),   name='showroom-reviews'),
    path('workshops/<int:workshop_id>/reviews/',      views.WorkshopReviewsView.as_view(),   name='workshop-reviews'),
    # Importer order reviews
    path('orders/<int:order_id>/review/',             views.OrderReviewView.as_view(),       name='order-review'),
    # NOTE: importers/<pk>/reviews/ is served by the importers app (list + create),
    # which matches the frontend's expected array/paginated response shape.
    # The richer summary view remains available at the route below.
    path('importers/<int:importer_id>/reviews/summary/', views.ImporterReviewsView.as_view(), name='importer-reviews-summary'),
    # Admin
    path('admin/reviews/',                            views.AdminReviewListView.as_view(),   name='admin-review-list'),
    path('admin/reviews/stats/',                      views.AdminReviewStatsView.as_view(),  name='admin-review-stats'),
    path('admin/reviews/<int:pk>/',                   views.AdminReviewDetailView.as_view(), name='admin-review-detail'),
]
