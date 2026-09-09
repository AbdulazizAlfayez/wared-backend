from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from drf_spectacular.utils import extend_schema

from .models import Favorite
from cars.models import Listing
from cars.serializers import ListingSerializer


class FavoritesPagination(PageNumberPagination):
    page_size = 20


@extend_schema(tags=['Favorites'])
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def toggle_favorite(request, listing_id):
    """Toggle favorite on/off for a listing. Returns { is_favorited: bool }."""
    if not Listing.objects.filter(id=listing_id).exists():
        return Response({'error': 'Listing not found'}, status=status.HTTP_404_NOT_FOUND)

    fav, created = Favorite.objects.get_or_create(
        user=request.user, listing_id=listing_id
    )
    if not created:
        fav.delete()
        return Response({'is_favorited': False})
    return Response({'is_favorited': True}, status=status.HTTP_201_CREATED)


@extend_schema(tags=['Favorites'])
@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def favorites_list(request):
    """
    GET  /api/favorites/              - paginated list of user's favorited listings.
    GET  /api/favorites/?listing=<id> - favorite wrapper(s) for one listing:
                                        {results: [{id, listing: {id}}]}
    POST /api/favorites/ {listing}    - create favorite, returns {id, listing: {id}} (201).
    """
    if request.method == 'POST':
        listing_id = request.data.get('listing') or request.data.get('listing_id')
        try:
            listing_id = int(listing_id)
        except (TypeError, ValueError):
            return Response({'error': 'listing is required.'}, status=status.HTTP_400_BAD_REQUEST)
        if not Listing.objects.filter(id=listing_id).exists():
            return Response({'error': 'Listing not found'}, status=status.HTTP_404_NOT_FOUND)
        fav, created = Favorite.objects.get_or_create(
            user=request.user, listing_id=listing_id
        )
        return Response(
            {'id': fav.id, 'listing': {'id': listing_id}, 'is_favorited': True},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    # GET with ?listing= filter → favorite-wrapper shape (used by the car page
    # to discover the favorite id for the delete call)
    listing_param = request.query_params.get('listing')
    if listing_param:
        try:
            listing_param = int(listing_param)
        except (TypeError, ValueError):
            return Response({'error': 'listing must be an integer.'}, status=status.HTTP_400_BAD_REQUEST)
        favs = Favorite.objects.filter(user=request.user, listing_id=listing_param)
        return Response({
            'count': favs.count(),
            'results': [{'id': f.id, 'listing': {'id': f.listing_id}} for f in favs],
        })
    fav_listing_ids = (
        Favorite.objects.filter(user=request.user)
        .order_by('-created_at')
        .values_list('listing_id', flat=True)
    )
    # Preserve favorite ordering
    listings = Listing.objects.filter(id__in=fav_listing_ids).select_related(
        'owner', 'city_obj'
    ).prefetch_related('images')

    # Manual ordering to match favorite creation order
    id_order = {lid: idx for idx, lid in enumerate(fav_listing_ids)}
    listings = sorted(listings, key=lambda l: id_order.get(l.id, 0))

    paginator = FavoritesPagination()
    page = paginator.paginate_queryset(listings, request)
    serializer = ListingSerializer(page, many=True, context={'request': request})
    return paginator.get_paginated_response(serializer.data)


@extend_schema(tags=['Favorites'])
@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def favorite_delete(request, pk):
    """DELETE /api/favorites/<pk>/ - remove one of the user's favorites."""
    try:
        fav = Favorite.objects.get(pk=pk, user=request.user)
    except Favorite.DoesNotExist:
        return Response({'error': 'Favorite not found'}, status=status.HTTP_404_NOT_FOUND)
    fav.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=['Favorites'])
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def favorites_count(request):
    """GET /api/favorites/count/ - total favorite count for badge."""
    count = Favorite.objects.filter(user=request.user).count()
    return Response({'count': count})
