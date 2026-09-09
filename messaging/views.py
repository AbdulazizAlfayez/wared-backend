from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema

from .models import BlockedUser, Conversation, Message
from .serializers import (
    BlockedUserSerializer,
    ConversationDetailSerializer,
    ConversationListSerializer,
    MessageCreateSerializer,
    MessageSerializer,
    StartConversationSerializer,
)
from .utils import mask_contact_info


def _is_participant(conversation, user):
    return conversation.buyer_id == user.pk or conversation.seller_id == user.pk


def _broadcast_message(message, conversation):
    """Broadcast a new message over WebSocket to the other party."""
    try:
        from channels.layers import get_channel_layer
        from asgiref.sync import async_to_sync
        from django.contrib.auth import get_user_model
        from .utils import get_allow_contact

        channel_layer = get_channel_layer()
        recipient_id = (
            conversation.seller_id if message.sender_id == conversation.buyer_id
            else conversation.buyer_id
        )
        image_url = None
        if message.image:
            try:
                image_url = message.image.url
            except Exception:
                pass

        # Determine the content to send over the socket.
        User = get_user_model()
        try:
            recipient = User.objects.get(pk=recipient_id)
            is_admin = recipient.is_staff
        except User.DoesNotExist:
            is_admin = False

        if is_admin or get_allow_contact(conversation):
            ws_content = message.content
        else:
            ws_content, _ = mask_contact_info(message.content)

        async_to_sync(channel_layer.group_send)(
            f'user_{recipient_id}',
            {
                'type': 'chat.message',
                'conversation_id': conversation.pk,
                'message': {
                    'id': message.pk,
                    'sender_id': message.sender_id,
                    'content': ws_content,
                    'image_url': image_url,
                    'message_type': message.message_type,
                    'is_system': message.is_system,
                    'created_at': message.created_at.isoformat(),
                },
            },
        )
    except Exception:
        pass  # WebSocket broadcast is best-effort


@extend_schema(tags=['Messaging'])
class ConversationViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsAuthenticated]
    http_method_names  = ['get', 'post', 'patch', 'head', 'options']

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return ConversationDetailSerializer
        return ConversationListSerializer

    def get_queryset(self):
        user = self.request.user
        qs = (
            Conversation.objects
            .filter(Q(buyer=user) | Q(seller=user))
            .select_related('listing', 'buyer', 'seller')
            .prefetch_related('listing__images', 'messages')
        )
        if self.action == 'list':
            buyer_qs  = qs.filter(buyer=user,  buyer_archived=False)
            seller_qs = qs.filter(seller=user, seller_archived=False)
            qs = (buyer_qs | seller_qs).distinct()
        return qs.order_by('-last_message_at', '-updated_at')

    # -- Create (with reservation gate) ----------------------------------------

    def create(self, request, *args, **kwargs):
        serializer = StartConversationSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)

        listing     = serializer.validated_data['listing']
        seller      = serializer.validated_data['seller']
        reservation = serializer.validated_data.get('reservation')
        buyer       = serializer.validated_data.get('buyer', request.user)

        try:
            conv, created = Conversation.objects.get_or_create(
                listing=listing,
                buyer=buyer,
                seller=seller,
                defaults={'reservation': reservation},
            )
        except IntegrityError:
            conv = Conversation.objects.get(listing=listing, buyer=buyer, seller=seller)
            created = False

        if created:
            # Notify the OTHER party (not always the seller)
            recipient = seller if request.user.pk == buyer.pk else buyer
            starter   = request.user
            try:
                from notifications.utils import notify
                notify(
                    recipient=recipient,
                    notification_type='conversation_started',
                    title='محادثة جديدة',
                    message=f'{starter.name or starter.email} بدأ محادثة حول "{listing.title}".',
                    listing=listing,
                    metadata={'conversation_id': conv.pk},
                )
            except Exception:
                pass

        out = ConversationDetailSerializer(conv, context={'request': request})
        return Response(out.data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    # -- Retrieve (marks unread as read) ----------------------------------------

    def retrieve(self, request, *args, **kwargs):
        conv = self.get_object()
        if not _is_participant(conv, request.user):
            return Response(status=status.HTTP_404_NOT_FOUND)

        # Mark as read
        now = timezone.now()
        Message.objects.filter(
            conversation=conv, is_read=False,
        ).exclude(sender=request.user).update(is_read=True, read_at=now)
        # Reset unread count
        if conv.buyer_id == request.user.pk:
            Conversation.objects.filter(pk=conv.pk).update(unread_count_buyer=0)
        else:
            Conversation.objects.filter(pk=conv.pk).update(unread_count_importer=0)
        conv.refresh_from_db()

        return Response(ConversationDetailSerializer(conv, context={'request': request}).data)

    # -- Archive/unarchive/close ------------------------------------------------

    def update(self, request, *args, **kwargs):
        conv = self.get_object()
        if not _is_participant(conv, request.user):
            return Response(status=status.HTTP_404_NOT_FOUND)

        user = request.user
        if 'buyer_archived' in request.data and conv.buyer_id == user.pk:
            conv.buyer_archived = bool(request.data['buyer_archived'])
        if 'seller_archived' in request.data and conv.seller_id == user.pk:
            conv.seller_archived = bool(request.data['seller_archived'])
        if 'is_active' in request.data:
            conv.is_active = bool(request.data['is_active'])
        conv.save()

        return Response(ConversationDetailSerializer(conv, context={'request': request}).data)

    def partial_update(self, request, *args, **kwargs):
        return self.update(request, *args, **kwargs)

    # -- Messages (GET = list, POST = send) ------------------------------------

    @action(detail=True, methods=['get', 'post'], url_path='messages')
    def messages(self, request, pk=None):
        conv = self.get_object()
        if not _is_participant(conv, request.user):
            return Response(status=status.HTTP_404_NOT_FOUND)

        if request.method == 'GET':
            return self._list_messages(request, conv)
        return self._send_message(request, conv)

    def _list_messages(self, request, conv):
        now = timezone.now()
        Message.objects.filter(
            conversation=conv, is_read=False,
        ).exclude(sender=request.user).update(is_read=True, read_at=now)

        # Reset my unread count
        if conv.buyer_id == request.user.pk:
            Conversation.objects.filter(pk=conv.pk).update(unread_count_buyer=0)
        else:
            Conversation.objects.filter(pk=conv.pk).update(unread_count_importer=0)

        # Cursor pagination via ?before=
        qs = conv.messages.select_related('sender').order_by('-created_at')
        before = request.query_params.get('before')
        if before:
            try:
                qs = qs.filter(pk__lt=int(before))
            except (ValueError, TypeError):
                pass
        msgs = list(qs[:50])
        msgs.reverse()
        serializer = MessageSerializer(msgs, many=True, context={'request': request})
        data = serializer.data

        # Cross-message aggregation: detect phone numbers split across
        # consecutive messages from the same sender
        from .utils import get_allow_contact, mask_conversation_messages
        is_admin = request.user.is_staff
        allow = is_admin or get_allow_contact(conv)
        if not allow and len(msgs) >= 2:
            masked_contents = mask_conversation_messages(
                msgs, reader_id=request.user.pk,
                allow_contact=False, is_admin=False,
            )
            for i, masked in enumerate(masked_contents):
                data[i]['content'] = masked

        return Response(data)

    @staticmethod
    def _contact_exchange_allowed(conv) -> bool:
        """Phone numbers / contact info may only be exchanged after the full
        balance payment is confirmed on the order between these two parties
        for this car. Until then messages are stored MASKED."""
        try:
            from payments.models import PaymentTransaction
            return PaymentTransaction.objects.filter(
                order__car=conv.listing,
                order__buyer=conv.buyer,
                order__importer=conv.seller,
                payment_type='balance',
                status='succeeded',
            ).exists()
        except Exception:
            return False

    def _send_message(self, request, conv):
        if not conv.is_active:
            return Response({'error': 'المحادثة مغلقة.'}, status=status.HTTP_400_BAD_REQUEST)

        sender    = request.user
        recipient = conv.seller if conv.buyer_id == sender.pk else conv.buyer

        if BlockedUser.objects.filter(blocker=recipient, blocked=sender).exists():
            return Response({'error': 'لا يمكنك مراسلة هذا المستخدم.'}, status=status.HTTP_403_FORBIDDEN)

        serializer = MessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        msg_type = serializer.validated_data.get('message_type', 'text')
        content  = serializer.validated_data.get('content', '')

        masked_content, was_flagged = mask_contact_info(content)
        # Store the MASKED text unless full payment already happened between
        # these parties for this car — this is what actually prevents phone
        # numbers from being exchanged before WARED receives the payment.
        if self._contact_exchange_allowed(conv):
            stored_content = content
        else:
            # Second pass: catch phone numbers split across messages
            # ("055584" then "0131") — any bare digit run gets masked too.
            from .utils import strict_digit_mask
            stored_content, strict_flagged = strict_digit_mask(masked_content)
            was_flagged = was_flagged or strict_flagged
        msg = Message.objects.create(
            conversation=conv,
            sender=sender,
            content=stored_content,
            message_type=msg_type,
            image=request.FILES.get('image') if msg_type == 'image' else None,
            contains_contact_attempt=was_flagged,
        )

        conv.update_last_message(msg)
        _broadcast_message(msg, conv)

        # Notify recipient
        try:
            from notifications.utils import notify
            notify(
                recipient=recipient,
                notification_type='new_message',
                title='رسالة جديدة',
                message=f'{sender.name or sender.email}: {content[:60]}',
                listing=conv.listing,
                metadata={'conversation_id': conv.pk},
            )
        except Exception:
            pass

        # Email notification (task itself respects the user's email preferences)
        try:
            from notifications.tasks import send_new_message_email
            send_new_message_email.delay(msg.pk)
        except Exception:
            pass
        # TODO: push notification trigger here (Phase M-later)

        return Response(
            MessageSerializer(msg, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )

    # -- Mark read -------------------------------------------------------------

    @action(detail=True, methods=['post'], url_path='mark-read')
    def mark_read(self, request, pk=None):
        conv = self.get_object()
        if not _is_participant(conv, request.user):
            return Response(status=status.HTTP_404_NOT_FOUND)
        now = timezone.now()
        Message.objects.filter(
            conversation=conv, is_read=False,
        ).exclude(sender=request.user).update(is_read=True, read_at=now)
        if conv.buyer_id == request.user.pk:
            Conversation.objects.filter(pk=conv.pk).update(unread_count_buyer=0)
        else:
            Conversation.objects.filter(pk=conv.pk).update(unread_count_importer=0)
        return Response({'detail': 'تم التحديث.'})

    # -- Unread total ----------------------------------------------------------

    @action(detail=False, methods=['get'], url_path='unread-total')
    def unread_total(self, request):
        user = request.user
        count = (
            Message.objects
            .filter(
                conversation__in=Conversation.objects.filter(Q(buyer=user) | Q(seller=user)),
                is_read=False,
            )
            .exclude(sender=user)
            .count()
        )
        return Response({'unread_total': count})


# ---------------------------------------------------------------------------
# BlockedUser ViewSet
# ---------------------------------------------------------------------------

@extend_schema(tags=['Messaging'])
class BlockedUserViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsAuthenticated]
    serializer_class   = BlockedUserSerializer

    def get_queryset(self):
        return BlockedUser.objects.filter(blocker=self.request.user).select_related('blocked').order_by('-created_at')

    def create(self, request, *args, **kwargs):
        user_id = request.data.get('user_id')
        reason  = request.data.get('reason', '')
        if not user_id:
            return Response({'error': 'user_id مطلوب.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            user_id = int(user_id)
        except (ValueError, TypeError):
            return Response({'error': 'user_id غير صالح.'}, status=status.HTTP_400_BAD_REQUEST)
        if user_id == request.user.pk:
            return Response({'error': 'لا يمكنك حظر نفسك.'}, status=status.HTTP_400_BAD_REQUEST)
        from django.contrib.auth import get_user_model
        User = get_user_model()
        try:
            blocked_user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return Response({'error': 'المستخدم غير موجود.'}, status=status.HTTP_404_NOT_FOUND)
        block, created = BlockedUser.objects.get_or_create(
            blocker=request.user, blocked=blocked_user, defaults={'reason': reason},
        )
        if not created:
            return Response({'error': 'تم حظر هذا المستخدم سابقاً.'}, status=status.HTTP_400_BAD_REQUEST)
        Conversation.objects.filter(
            Q(buyer=request.user, seller=blocked_user) | Q(buyer=blocked_user, seller=request.user),
            is_active=True,
        ).update(is_active=False)
        return Response(BlockedUserSerializer(block, context={'request': request}).data, status=status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        self.get_object().delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
