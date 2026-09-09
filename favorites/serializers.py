from rest_framework import serializers
from .models import Favorite
from cars.serializers import CarSerializer


class FavoriteSerializer(serializers.ModelSerializer):
    """Serializer for Favorite model."""
    
    car = CarSerializer(read_only=True)
    car_id = serializers.IntegerField(write_only=True)
    
    class Meta:
        model = Favorite
        fields = ('id', 'car', 'car_id', 'created_at')
        read_only_fields = ('id', 'created_at')
    
    def create(self, validated_data):
        """Create favorite for current user."""
        validated_data['user'] = self.context['request'].user
        return super().create(validated_data)


class FavoriteListSerializer(serializers.ModelSerializer):
    """Serializer for Favorite list."""
    
    car = CarSerializer(read_only=True)
    
    class Meta:
        model = Favorite
        fields = ('id', 'car', 'created_at')
        read_only_fields = ('id', 'created_at')



