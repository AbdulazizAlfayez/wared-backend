from django.conf import settings
from rest_framework import serializers

from .models import Conversation, Message


class ChatRequestSerializer(serializers.Serializer):
    message = serializers.CharField(
        max_length=settings.ASSISTANT_MESSAGE_MAX_CHARS,
    )
    conversation_id = serializers.IntegerField(required=False)
    history = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        max_length=settings.ASSISTANT_HISTORY_LIMIT,
    )

    def validate_message(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('Message cannot be empty.')
        return value

    def validate_history(self, value):
        max_chars = settings.ASSISTANT_MESSAGE_MAX_CHARS
        for i, item in enumerate(value):
            if item.get('role') not in ('user', 'assistant'):
                raise serializers.ValidationError(
                    f'History item {i}: role must be "user" or "assistant".'
                )
            content = item.get('content', '')
            if not isinstance(content, str) or len(content) > max_chars:
                raise serializers.ValidationError(
                    f'History item {i}: content must be a string of at most {max_chars} characters.'
                )
        return value


class CarCardSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    make = serializers.CharField()
    model = serializers.CharField()
    year = serializers.IntegerField()
    price = serializers.CharField()
    source_country = serializers.CharField(allow_blank=True)
    city = serializers.CharField(allow_blank=True)
    image = serializers.CharField(allow_null=True)
    url = serializers.CharField()


class CarsMetaSerializer(serializers.Serializer):
    total_matches = serializers.IntegerField()
    params = serializers.DictField()


class OrderCardSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    order_number = serializers.CharField()
    car = serializers.CharField()
    image = serializers.CharField(allow_null=True)
    status = serializers.CharField()
    payment_status = serializers.CharField()
    url = serializers.CharField()


class ChatResponseSerializer(serializers.Serializer):
    reply = serializers.CharField()
    conversation_id = serializers.IntegerField(required=False)
    cars = CarCardSerializer(many=True)
    cars_meta = CarsMetaSerializer(allow_null=True)
    orders = OrderCardSerializer(many=True)


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = ('id', 'role', 'content', 'meta', 'created_at')
        read_only_fields = fields


class ConversationListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Conversation
        fields = ('id', 'title', 'updated_at')
        read_only_fields = fields


class ConversationDetailSerializer(serializers.ModelSerializer):
    messages = MessageSerializer(many=True, read_only=True)

    class Meta:
        model = Conversation
        fields = ('id', 'title', 'created_at', 'updated_at', 'messages')
        read_only_fields = fields
