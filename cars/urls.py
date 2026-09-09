from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    CarViewSet,
    CarImageViewSet,
    ListingViewSet,
    ListingImageViewSet,
    SavedSearchViewSet,
    RecentlyViewedView,
    RecentlyViewedDetailView,
    ShowroomViewSet,
    WorkshopViewSet,
    BulkTemplateView,
    BulkUploadCreateView,
    BulkUploadListView,
    BulkUploadDetailView,
    BulkStatusChangeView,
    BulkDeleteView,
    BulkExportView,
    MyPromotionsView,
    ImportedCarListView,
    ImportedCarArrivingView,
    ImportedCarDetailView,
    ImportedCarFilterOptionsView,
    AdminConfirmPromotionPaymentView,
    AdminRejectPromotionPaymentView,
)
from favorites.views import toggle_favorite as toggle_favorite_view
from fraud.views import UserListingLimitView

listing_router = DefaultRouter()
listing_router.register(r'', ListingViewSet, basename='listing')

car_router = DefaultRouter()
car_router.register(r'', CarViewSet, basename='car')
car_router.register(r'images', CarImageViewSet, basename='car-image')

saved_search_router = DefaultRouter()
saved_search_router.register(r'', SavedSearchViewSet, basename='saved-search')

showroom_router = DefaultRouter()
showroom_router.register(r'', ShowroomViewSet, basename='showroom')

workshop_router = DefaultRouter()
workshop_router.register(r'', WorkshopViewSet, basename='workshop')

urlpatterns = [
    # -----------------------------------------------------------------------
    # Phase 4.5 — Bulk Operations (must come BEFORE router include to avoid
    # "bulk" being matched as a listing pk)
    # -----------------------------------------------------------------------
    path('listings/bulk/template/',          BulkTemplateView.as_view(),      name='bulk-template'),
    path('listings/bulk/upload/',            BulkUploadCreateView.as_view(),  name='bulk-upload'),
    path('listings/bulk/uploads/',           BulkUploadListView.as_view(),    name='bulk-upload-list'),
    path('listings/bulk/uploads/<int:pk>/',  BulkUploadDetailView.as_view(),  name='bulk-upload-detail'),
    path('listings/bulk/status/',            BulkStatusChangeView.as_view(),  name='bulk-status'),
    path('listings/bulk/delete/',            BulkDeleteView.as_view(),        name='bulk-delete'),
    path('listings/bulk/export/',            BulkExportView.as_view(),        name='bulk-export'),

    # Promotion payment verification (WARED admin)
    path('admin/promotion-payments/<int:txn_id>/confirm/', AdminConfirmPromotionPaymentView.as_view(), name='admin-promo-pay-confirm'),
    path('admin/promotion-payments/<int:txn_id>/reject/',  AdminRejectPromotionPaymentView.as_view(),  name='admin-promo-pay-reject'),

    # Daily listing limit — must also come BEFORE the router include, otherwise
    # the router's listings/<pk>/ pattern swallows "limit" as a pk → 404.
    # (The same path in fraud/urls.py is unreachable because cars is included first.)
    path('listings/limit/',                  UserListingLimitView.as_view(),  name='listing-limit'),

    # Listing CRUD + approve/reject/my/compare/autocomplete actions (router-generated)
    path('listings/', include(listing_router.urls)),

    # Nested listing images — manual patterns so listing_id is captured cleanly.
    # These must come AFTER the include() so the router gets first crack at
    # /listings/<pk>/ style paths, and Django falls through to these only when
    # the extra /images/ segment is present.
    path(
        'listings/<int:listing_id>/images/',
        ListingImageViewSet.as_view(),
        name='listing-images-list',
    ),
    path(
        'listings/<int:listing_id>/images/<int:pk>/',
        ListingImageViewSet.as_view(),
        name='listing-images-detail',
    ),

    # Car endpoints
    path('cars/my/listings/', CarViewSet.as_view({'get': 'my_listings'}), name='my-listings'),
    path('cars/', include(car_router.urls)),

    # Saved searches (CRUD + /results/ action via router)
    path('saved-searches/', include(saved_search_router.urls)),

    # Recently viewed
    path('recently-viewed/', RecentlyViewedView.as_view(), name='recently-viewed-list'),
    path('recently-viewed/<int:pk>/', RecentlyViewedDetailView.as_view(), name='recently-viewed-detail'),

    # Phase 4.6 — My Promotions
    path('my-promotions/', MyPromotionsView.as_view(), name='my-promotions'),

    # Phase 2.15 — Showrooms & Workshops (read-only + nearby + map-pins)
    path('showrooms/', include(showroom_router.urls)),
    path('workshops/', include(workshop_router.urls)),

    # Import-specific public endpoints
    path('imported-cars/filter-options/', ImportedCarFilterOptionsView.as_view(), name='imported-car-filter-options'),
    path('imported-cars/arriving/',       ImportedCarArrivingView.as_view(),      name='imported-car-arriving'),
    path('imported-cars/<int:listing_id>/favorite/', toggle_favorite_view,        name='imported-car-favorite'),
    path('imported-cars/<int:pk>/',       ImportedCarDetailView.as_view(),        name='imported-car-detail'),
    path('imported-cars/',                ImportedCarListView.as_view(),          name='imported-car-list'),
]
