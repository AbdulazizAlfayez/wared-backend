"""
Tests for the WARED AI assistant app.

All tests mock the Anthropic client — they never hit the real API.
"""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import User
from cars.models import Listing
from orders.models import ImportOrder

from .models import Conversation, Message
from .services import (
    _execute_get_order_details,
    _execute_search_cars,
    run_chat,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_text_block(text):
    return SimpleNamespace(type='text', text=text)


def _make_tool_use_block(tool_id, name, input_data):
    return SimpleNamespace(type='tool_use', id=tool_id, name=name, input=input_data)


def _make_response(content_blocks, stop_reason='end_turn', input_tokens=10, output_tokens=20):
    return SimpleNamespace(
        content=content_blocks,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def _mock_client_factory(responses):
    client = MagicMock()
    client.messages.create.side_effect = list(responses)
    return client


def _create_test_user(email='buyer@test.com', role='user', name='Test Buyer'):
    return User.objects.create_user(
        email=email, password='testpass123', role=role, name=name,
    )


def _create_importer(email='importer@test.com', name='Test Importer'):
    return User.objects.create_user(
        email=email, password='testpass123', role='importer', name=name,
    )


def _create_listing(owner, **kwargs):
    defaults = dict(
        title='Test Car', make='Toyota', model='Camry', year=2024,
        price=80000, mileage=0, condition='new', status='approved',
        is_active=True, import_status='available',
    )
    defaults.update(kwargs)
    return Listing.objects.create(owner=owner, **defaults)


def _create_order(car, buyer, importer, **kwargs):
    defaults = dict(status='confirmed')
    defaults.update(kwargs)
    return ImportOrder.objects.create(
        car=car, buyer=buyer, importer=importer, **defaults,
    )


# ---------------------------------------------------------------------------
# 1. Anonymous chat — new response shape
# ---------------------------------------------------------------------------

class AnonymousChatTest(APITestCase):
    @patch('assistant.services.get_anthropic_client')
    def test_anonymous_chat_returns_reply_with_empty_cards(self, mock_factory):
        client = _mock_client_factory([
            _make_response([_make_text_block('Hello! How can I help?')])
        ])
        mock_factory.return_value = client

        resp = self.client.post('/api/assistant/chat/', {
            'message': 'Hi there',
        }, format='json')

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['reply'], 'Hello! How can I help?')
        self.assertNotIn('conversation_id', resp.data)
        # New shape: always has cars, cars_meta, orders
        self.assertEqual(resp.data['cars'], [])
        self.assertIsNone(resp.data['cars_meta'])
        self.assertEqual(resp.data['orders'], [])

        # No order tools passed
        call_kwargs = client.messages.create.call_args
        tool_names = [t['name'] for t in call_kwargs.kwargs.get('tools', call_kwargs[1].get('tools', []))]
        self.assertIn('search_cars', tool_names)
        self.assertNotIn('get_my_orders', tool_names)

    @patch('assistant.services.get_anthropic_client')
    def test_anonymous_response_never_has_orders(self, mock_factory):
        """Even if the model somehow returned order data, anon gets empty orders."""
        # search_cars returns cars; no orders possible for anon
        tool_resp = _make_response(
            [_make_tool_use_block('c1', 'search_cars', {'make': 'Toyota'})],
            stop_reason='tool_use',
        )
        final_resp = _make_response([_make_text_block('Found cars!')])
        client = _mock_client_factory([tool_resp, final_resp])
        mock_factory.return_value = client

        importer = _create_importer()
        _create_listing(importer)

        resp = self.client.post('/api/assistant/chat/', {
            'message': 'Find Toyota',
        }, format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['orders'], [])
        self.assertGreaterEqual(len(resp.data['cars']), 1)


# ---------------------------------------------------------------------------
# 2. Authenticated chat — conversation persistence + cards
# ---------------------------------------------------------------------------

class AuthenticatedChatTest(APITestCase):
    def setUp(self):
        self.user = _create_test_user()
        self.client.force_authenticate(self.user)

    @patch('assistant.services.get_anthropic_client')
    def test_creates_conversation_and_messages(self, mock_factory):
        client = _mock_client_factory([
            _make_response([_make_text_block('Sure, let me help!')])
        ])
        mock_factory.return_value = client

        resp = self.client.post('/api/assistant/chat/', {
            'message': 'What cars do you have?',
        }, format='json')

        self.assertEqual(resp.status_code, 200)
        conv_id = resp.data['conversation_id']
        self.assertIsNotNone(conv_id)
        self.assertIn('cars', resp.data)
        self.assertIn('orders', resp.data)

        conv = Conversation.objects.get(pk=conv_id)
        self.assertEqual(conv.user, self.user)
        self.assertEqual(conv.messages.count(), 2)

    @patch('assistant.services.get_anthropic_client')
    def test_appends_to_existing_conversation(self, mock_factory):
        client = _mock_client_factory([
            _make_response([_make_text_block('Reply 1')]),
            _make_response([_make_text_block('Reply 2')]),
        ])
        mock_factory.return_value = client

        resp1 = self.client.post('/api/assistant/chat/', {
            'message': 'First message',
        }, format='json')
        conv_id = resp1.data['conversation_id']

        resp2 = self.client.post('/api/assistant/chat/', {
            'message': 'Second message',
            'conversation_id': conv_id,
        }, format='json')

        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp2.data['conversation_id'], conv_id)
        self.assertEqual(Message.objects.filter(conversation_id=conv_id).count(), 4)


# ---------------------------------------------------------------------------
# 3. Cross-user isolation
# ---------------------------------------------------------------------------

class CrossUserIsolationTest(APITestCase):
    def setUp(self):
        self.user_a = _create_test_user(email='a@test.com', name='User A')
        self.user_b = _create_test_user(email='b@test.com', name='User B')

    @patch('assistant.services.get_anthropic_client')
    def test_user_b_cannot_use_user_a_conversation(self, mock_factory):
        client = _mock_client_factory([_make_response([_make_text_block('OK')])])
        mock_factory.return_value = client

        self.client.force_authenticate(self.user_a)
        resp = self.client.post('/api/assistant/chat/', {'message': 'Hi'}, format='json')
        conv_id = resp.data['conversation_id']

        self.client.force_authenticate(self.user_b)
        resp = self.client.post('/api/assistant/chat/', {
            'message': 'Hijack', 'conversation_id': conv_id,
        }, format='json')
        self.assertEqual(resp.status_code, 404)

    @patch('assistant.services.get_anthropic_client')
    def test_user_b_cannot_get_user_a_conversation_detail(self, mock_factory):
        client = _mock_client_factory([_make_response([_make_text_block('OK')])])
        mock_factory.return_value = client

        self.client.force_authenticate(self.user_a)
        resp = self.client.post('/api/assistant/chat/', {'message': 'Hi'}, format='json')
        conv_id = resp.data['conversation_id']

        self.client.force_authenticate(self.user_b)
        resp = self.client.get(f'/api/assistant/conversations/{conv_id}/')
        self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# 4. get_order_details — ownership + no sensitive fields
# ---------------------------------------------------------------------------

class OrderDetailsExecutorTest(TestCase):
    def setUp(self):
        self.importer = _create_importer()
        self.buyer = _create_test_user()
        self.stranger = _create_test_user(email='stranger@test.com', name='Stranger')
        self.car = _create_listing(self.importer)
        self.order = _create_order(self.car, self.buyer, self.importer)

    def test_buyer_gets_order_data(self):
        result = _execute_get_order_details({'order_id': self.order.id}, self.buyer)
        self.assertIn('order_number', result)
        self.assertNotIn('error', result)

    def test_importer_gets_order_data(self):
        result = _execute_get_order_details({'order_id': self.order.id}, self.importer)
        self.assertIn('order_number', result)

    def test_stranger_gets_not_found(self):
        result = _execute_get_order_details({'order_id': self.order.id}, self.stranger)
        self.assertEqual(result, {'error': 'not found'})

    def test_no_sensitive_fields_in_result(self):
        result = _execute_get_order_details({'order_id': self.order.id}, self.buyer)
        result_str = json.dumps(result).lower()
        self.assertNotIn('iban', result_str)
        self.assertNotIn('phone', result_str)
        self.assertNotIn('email', result_str)
        self.assertNotIn('beneficiary', result_str)

    def test_order_has_image_and_url(self):
        result = _execute_get_order_details({'order_id': self.order.id}, self.buyer)
        self.assertIn('image', result)
        self.assertIn('url', result)
        self.assertEqual(result['url'], f"/orders/{self.order.id}")


# ---------------------------------------------------------------------------
# 5. search_cars — never returns reserved/pending/draft + image key
# ---------------------------------------------------------------------------

class SearchCarsExecutorTest(TestCase):
    def setUp(self):
        self.importer = _create_importer()

    def test_excludes_non_public_listings(self):
        _create_listing(self.importer, title='Public', status='approved', import_status='available')
        _create_listing(self.importer, title='Reserved', status='approved', import_status='reserved')
        _create_listing(self.importer, title='Pending', status='pending', import_status='available')
        _create_listing(self.importer, title='Draft', status='draft', import_status='available')
        _create_listing(self.importer, title='Sold', status='sold', import_status='sold')

        result = _execute_search_cars({}, None)
        self.assertEqual(result['total_matches'], 1)
        self.assertEqual(len(result['cars']), 1)

    def test_car_has_image_key_null_safe(self):
        """Cars without images should have image=None, not crash."""
        _create_listing(self.importer)
        result = _execute_search_cars({}, None)
        self.assertEqual(len(result['cars']), 1)
        self.assertIn('image', result['cars'][0])
        # No images uploaded → image is None
        self.assertIsNone(result['cars'][0]['image'])


# ---------------------------------------------------------------------------
# 6. Validation
# ---------------------------------------------------------------------------

class ValidationTest(APITestCase):
    def test_message_over_limit(self):
        long_msg = 'a' * (settings.ASSISTANT_MESSAGE_MAX_CHARS + 1)
        resp = self.client.post('/api/assistant/chat/', {
            'message': long_msg,
        }, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_history_over_limit(self):
        history = [{'role': 'user', 'content': 'x'}] * (settings.ASSISTANT_HISTORY_LIMIT + 1)
        resp = self.client.post('/api/assistant/chat/', {
            'message': 'hi', 'history': history,
        }, format='json')
        self.assertEqual(resp.status_code, 400)


# ---------------------------------------------------------------------------
# 7. Empty API key → 503
# ---------------------------------------------------------------------------

class MissingKeyTest(APITestCase):
    @override_settings(ANTHROPIC_API_KEY='')
    def test_empty_key_returns_503(self):
        resp = self.client.post('/api/assistant/chat/', {
            'message': 'hello',
        }, format='json')
        self.assertEqual(resp.status_code, 503)
        self.assertIn('المساعد', resp.data['detail'])


# ---------------------------------------------------------------------------
# 8. Throttling
# ---------------------------------------------------------------------------

class ThrottleTest(APITestCase):
    @patch('assistant.services.get_anthropic_client')
    def test_anon_throttle(self, mock_factory):
        client = _mock_client_factory(
            [_make_response([_make_text_block('OK')])] * 15
        )
        mock_factory.return_value = client

        from django.core.cache import cache
        cache.clear()

        from assistant.views import AssistantAnonThrottle
        AssistantAnonThrottle.THROTTLE_RATES['assistant_anon'] = '2/hour'
        try:
            statuses = []
            for _ in range(4):
                resp = self.client.post('/api/assistant/chat/', {
                    'message': 'hi',
                }, format='json')
                statuses.append(resp.status_code)
            self.assertIn(429, statuses)
        finally:
            AssistantAnonThrottle.THROTTLE_RATES['assistant_anon'] = '10/hour'
            cache.clear()


# ---------------------------------------------------------------------------
# 9. Tool loop — cars populated, deduped, capped
# ---------------------------------------------------------------------------

class ToolLoopCardsTest(APITestCase):
    def setUp(self):
        self.importer = _create_importer()
        for i in range(8):
            _create_listing(self.importer, make=f'Brand{i}', model=f'M{i}')

    @patch('assistant.services.get_anthropic_client')
    def test_search_populates_cars_in_response(self, mock_factory):
        tool_resp = _make_response(
            [_make_tool_use_block('c1', 'search_cars', {'limit': 8})],
            stop_reason='tool_use',
        )
        final_resp = _make_response([_make_text_block('Here are some cars.')])
        client = _mock_client_factory([tool_resp, final_resp])
        mock_factory.return_value = client

        resp = self.client.post('/api/assistant/chat/', {
            'message': 'Show me cars',
        }, format='json')

        self.assertEqual(resp.status_code, 200)
        # Capped at 6
        self.assertLessEqual(len(resp.data['cars']), 6)
        self.assertGreater(len(resp.data['cars']), 0)
        # Each car has image key
        for car in resp.data['cars']:
            self.assertIn('image', car)
        # cars_meta populated
        self.assertIsNotNone(resp.data['cars_meta'])
        self.assertIn('total_matches', resp.data['cars_meta'])

    @patch('assistant.services.get_anthropic_client')
    def test_duplicate_search_calls_dedup_cars(self, mock_factory):
        """Two search_cars calls with same results → deduped."""
        tool_resp1 = _make_response(
            [_make_tool_use_block('c1', 'search_cars', {'make': 'Brand0'})],
            stop_reason='tool_use',
        )
        tool_resp2 = _make_response(
            [_make_tool_use_block('c2', 'search_cars', {'make': 'Brand0'})],
            stop_reason='tool_use',
        )
        final_resp = _make_response([_make_text_block('Done.')])
        client = _mock_client_factory([tool_resp1, tool_resp2, final_resp])
        mock_factory.return_value = client

        resp = self.client.post('/api/assistant/chat/', {
            'message': 'Search Brand0 twice',
        }, format='json')

        self.assertEqual(resp.status_code, 200)
        car_ids = [c['id'] for c in resp.data['cars']]
        # No duplicates
        self.assertEqual(len(car_ids), len(set(car_ids)))


# ---------------------------------------------------------------------------
# 10. Tool loop — orders with real data
# ---------------------------------------------------------------------------

class ToolLoopOrdersTest(APITestCase):
    def setUp(self):
        self.user = _create_test_user()
        self.importer = _create_importer()
        self.car = _create_listing(self.importer)
        self.order = _create_order(self.car, self.user, self.importer)
        self.client.force_authenticate(self.user)

    @patch('assistant.services.get_anthropic_client')
    def test_tool_loop_populates_orders(self, mock_factory):
        tool_response = _make_response(
            [_make_tool_use_block('call_1', 'get_my_orders', {})],
            stop_reason='tool_use',
        )
        final_response = _make_response([_make_text_block('You have 1 order.')])
        client = _mock_client_factory([tool_response, final_response])
        mock_factory.return_value = client

        resp = self.client.post('/api/assistant/chat/', {
            'message': 'Show my orders',
        }, format='json')

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['reply'], 'You have 1 order.')
        self.assertEqual(len(resp.data['orders']), 1)
        self.assertIn('image', resp.data['orders'][0])
        self.assertIn('url', resp.data['orders'][0])


# ---------------------------------------------------------------------------
# 11. Message.meta stores cards; conversation detail returns it
# ---------------------------------------------------------------------------

class MetaPersistenceTest(APITestCase):
    def setUp(self):
        self.user = _create_test_user()
        self.client.force_authenticate(self.user)

    @patch('assistant.services.get_anthropic_client')
    def test_assistant_message_stores_meta(self, mock_factory):
        importer = _create_importer()
        _create_listing(importer)

        tool_resp = _make_response(
            [_make_tool_use_block('c1', 'search_cars', {})],
            stop_reason='tool_use',
        )
        final_resp = _make_response([_make_text_block('Found cars!')])
        client = _mock_client_factory([tool_resp, final_resp])
        mock_factory.return_value = client

        resp = self.client.post('/api/assistant/chat/', {
            'message': 'Show cars',
        }, format='json')
        conv_id = resp.data['conversation_id']

        # Check the assistant Message has meta with cars
        assistant_msg = Message.objects.filter(
            conversation_id=conv_id, role='assistant',
        ).first()
        self.assertIsNotNone(assistant_msg)
        self.assertIn('cars', assistant_msg.meta)
        self.assertGreater(len(assistant_msg.meta['cars']), 0)

        # GET conversation detail returns meta
        detail_resp = self.client.get(f'/api/assistant/conversations/{conv_id}/')
        self.assertEqual(detail_resp.status_code, 200)
        msgs = detail_resp.data['messages']
        assistant_msgs = [m for m in msgs if m['role'] == 'assistant']
        self.assertGreater(len(assistant_msgs), 0)
        self.assertIn('cars', assistant_msgs[0]['meta'])
