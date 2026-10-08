import sys
import time
from pathlib import Path
import torch

model_dir = Path('/home/david/clef-flash-service/model')
sys.path.insert(0, str(model_dir))

from joint_schema_model import load_release_model, systemone

print(f'PyTorch threads: {torch.get_num_threads()}')
print('Loading model on CPU with bfloat16...')
t0 = time.time()
model, processor = load_release_model(model_dir, device='cpu', dtype=torch.bfloat16)
load_time = time.time() - t0
print(f'Model loaded in {load_time:.2f} seconds.')

test_request = {
    'model': 'clef-flash',
    'state': 'Our checkout started returning errors and orders are blocked.',
    'questions': {
        'department': {
            'type': 'choice',
            'instructions': 'Which team should handle the message?',
            'criteria': {'billing': 'Payments or invoices', 'technical': 'Bugs or outages'},
        },
        'urgency': {'type': 'score', 'criteria': ['Can wait', 'This week', 'Today']},
        'outage': {'type': 'noul', 'instructions': 'Is a service down?'},
    },
}

print('\n--- Running Test 1 (Cold inference) ---')
t1 = time.time()
res1 = systemone(model, processor, test_request)
lat1 = time.time() - t1
print(f'Test 1 completed in: {lat1:.3f}s ({lat1*1000:.1f} ms)')
print('Result:', res1.get('answers'))

print('\n--- Running Test 2 (Warm inference) ---')
t2 = time.time()
res2 = systemone(model, processor, test_request)
lat2 = time.time() - t2
print(f'Test 2 completed in: {lat2:.3f}s ({lat2*1000:.1f} ms)')
print('Result:', res2.get('answers'))

print('\n--- Running Test 3 (Warm inference) ---')
t3 = time.time()
res3 = systemone(model, processor, test_request)
lat3 = time.time() - t3
print(f'Test 3 completed in: {lat3:.3f}s ({lat3*1000:.1f} ms)')
print('Result:', res3.get('answers'))
