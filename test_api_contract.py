import copy
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient
from pydantic import ValidationError
import api_server as api


STRUCTURED = {
    'model': 'clef-flash',
    'state': {'invoice': {'status': 'paid', 'total': 1250}},
    'questions': {
        'status': {'type': 'choice', 'instructions': {'question': 'Payment status?'},
                   'criteria': {'paid': {'description': 'Paid'}, 'unpaid': None}},
        'large': {'type': 'noul', 'instructions': ['Total above 1000?'],
                  'criteria': {'true': {'description': 'Above 1000'}, 'false': ['1000 or less']}},
        'amount': {'type': 'score', 'instructions': {'question': 'Amount level?'},
                   'criteria': ['Low', {'description': 'Medium'}, ['High']]},
    },
}
RESULT = {
    'model': 'backend-model',
    'answers': {
        'status': {'type': 'choice', 'choice': 'paid', 'probabilities': {'paid': 0.9, 'unpaid': 0.1}, 'confidence': 0.8},
        'large': {'type': 'noul', 'noul': 0.9},
        'amount': {'type': 'score', 'score': 1.7, 'legend': {'0': 'Low', '1': {'description': 'Medium'}, '2': ['High']},
                   'probabilities': {'0': 0.1, '1': 0.1, '2': 0.8}, 'confidence': 0.7},
    },
    'usage': {'input_tokens': 100, 'output_tokens': 0},
}


class ContractTests(unittest.TestCase):
    def test_structured_values_and_null_descriptions_are_preserved(self):
        request = api.SystemOneRequest.model_validate(STRUCTURED)
        self.assertEqual(request.model_dump(exclude_unset=True), STRUCTURED)

    def test_structured_score_legend_is_preserved(self):
        answer = api.ScoreAnswer.model_validate(RESULT['answers']['amount'])
        self.assertEqual(answer.model_dump()['legend'], RESULT['answers']['amount']['legend'])

    def test_string_questions_remain_supported(self):
        payload = {'state': 'paid', 'questions': {'paid': {'type': 'noul', 'instructions': 'Paid?'}}}
        self.assertEqual(api.SystemOneRequest.model_validate(payload).questions['paid'].instructions, 'Paid?')

    def test_invalid_primitive_shapes_and_limits_are_rejected(self):
        bad_questions = [
            {'type': 'choice', 'criteria': {}},
            {'type': 'choice', 'criteria': ['a', 'b']},
            {'type': 'score', 'criteria': ['only one']},
            {'type': 'score', 'criteria': ['level'] * 11},
            {'type': 'noul', 'instructions': 42},
        ]
        for question in bad_questions:
            with self.subTest(question=question), self.assertRaises(ValidationError):
                api.SystemOneRequest.model_validate({'state': 'x', 'questions': {'q': question}})

    def test_empty_questions_and_wrong_model_are_rejected(self):
        for payload in ({'state': 'x', 'questions': {}}, {**STRUCTURED, 'model': 'jev-latest'}):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                api.SystemOneRequest.model_validate(payload)

    def test_openapi_publishes_structured_discriminated_question_types(self):
        schemas = api.app.openapi()['components']['schemas']
        self.assertIn('oneOf', schemas['SystemOneRequest']['properties']['questions']['additionalProperties'])
        for name in ('ChoiceQuestion', 'NoulQuestion', 'ScoreQuestion'):
            types = {entry.get('type') for entry in schemas[name]['properties']['instructions']['anyOf']}
            self.assertTrue({'string', 'object', 'array', 'null'}.issubset(types))

    def test_route_forwards_structured_input_and_preserves_structured_output(self):
        with patch.object(api, 'record_telemetry') as telemetry:
            with TestClient(api.app) as client:
                send = AsyncMock(return_value=httpx.Response(200, json=RESULT))
                with patch.object(api.http_client, 'post', send):
                    response = client.post('/v1/systemone', json=STRUCTURED)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(send.call_args.kwargs['json'], STRUCTURED)
        self.assertEqual(response.json()['answers'], RESULT['answers'])
        self.assertEqual(response.json()['model'], 'clef-flash-q4_k_m')
        self.assertIn('latency_seconds', response.json())
        telemetry.assert_called_once()

    def test_null_state_images_and_extra_fields_are_forwarded(self):
        payload = copy.deepcopy(STRUCTURED)
        payload.update(state=None, images=['data:image/png;base64,example'], custom_field={'value': 'preserve'})
        with patch.object(api, 'record_telemetry'):
            with TestClient(api.app) as client:
                send = AsyncMock(return_value=httpx.Response(200, json=RESULT))
                with patch.object(api.http_client, 'post', send):
                    response = client.post('/v1/systemone', json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(send.call_args.kwargs['json'], payload)

    def test_backend_batch_overflow_is_a_client_error(self):
        error = {'error': {'message': 'input (5143 tokens) is too large to process. increase the physical batch size (current batch size: 4096)'}}
        with patch.object(api, 'record_telemetry'):
            with TestClient(api.app) as client:
                send = AsyncMock(return_value=httpx.Response(500, json=error))
                with patch.object(api.http_client, 'post', send):
                    response = client.post('/v1/systemone', json=STRUCTURED)
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn('current batch size: 4096', response.json()['detail'])

    def test_validation_failure_stops_before_backend(self):
        with TestClient(api.app) as client:
            send = AsyncMock()
            with patch.object(api.http_client, 'post', send):
                response = client.post('/v1/systemone', json={'state': 'x', 'questions': {'q': {'type': 'score', 'criteria': ['one']}}})
        self.assertEqual(response.status_code, 422)
        send.assert_not_called()


    def test_health_uses_dedicated_health_client(self):
        with TestClient(api.app) as client:
            health_get = AsyncMock(return_value=httpx.Response(200, json={'status': 'ok'}))
            infer_get = AsyncMock()
            with patch.object(api.health_client, 'get', health_get), patch.object(api.http_client, 'get', infer_get):
                response = client.get('/health')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'healthy')
        self.assertEqual(response.json()['backend_status'], 'healthy')
        health_get.assert_awaited_once()
        infer_get.assert_not_called()

    def test_queue_full_returns_503_with_retry_after(self):
        with patch.object(api, 'record_telemetry') as telemetry, patch.object(api, 'INFERENCE_QUEUE_TIMEOUT', 0.01):
            with TestClient(api.app) as client:
                with patch.object(api, 'MAX_CONCURRENT_INFERENCE', 1):
                    # Exhaust all permits on the lifespan semaphore
                    while not api.inference_semaphore.locked():
                        api.inference_semaphore._value -= 1
                    send = AsyncMock()
                    with patch.object(api.http_client, 'post', send):
                        response = client.post('/v1/systemone', json=STRUCTURED)
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.headers.get('retry-after'), '5')
        send.assert_not_called()
        telemetry.assert_called_once()
        self.assertEqual(telemetry.call_args.kwargs['error_type'], 'InferenceQueueFull')

    def test_influx_tag_escaping(self):
        self.assertEqual(api._escape_influx_tag('host=a, b c'), 'host_a__b_c')


if __name__ == '__main__':
    unittest.main()
