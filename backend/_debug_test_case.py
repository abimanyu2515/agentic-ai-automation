import sys, json
sys.path.insert(0, r'E:\Agentic-AI-Automation')
from backend.utils.prompts import load_prompt
from backend.agents.test_case_agent import TestCaseListResponse

prompt = load_prompt(
    'test_case_prompt.txt',
    title='Chatbot Response Accuracy',
    description='Validate the chatbot ability to detect and respond to user input.',
    scenarios='1. Valid input detection (priority: high): Chatbot correctly detects when user provides valid input and responds appropriately.',
    count='1',
)

from litellm import completion

print('=== Sending to LLM ===')
raw = completion(
    model='ollama/llama3.1',
    messages=[
        {'role': 'system', 'content': 'You respond with a single valid JSON object and nothing else - no markdown fences, no commentary before or after it.'},
        {'role': 'user', 'content': prompt}
    ],
    api_base='http://localhost:11434',
    format='json'
)
content = raw.choices[0].message.content.strip()
print('RAW OUTPUT:')
print(repr(content[:1000]))
print()

try:
    parsed = json.loads(content)
    print('JSON PARSE: OK')
    print('Top-level keys:', list(parsed.keys()))
    if 'test_cases' in parsed:
        tcs = parsed['test_cases']
        print(f'Number of test cases: {len(tcs)}')
        for i, tc in enumerate(tcs):
            steps = tc.get('steps')
            if isinstance(steps, str):
                print(f'  TC {i}: steps is STRING (length {len(steps)})')
            elif isinstance(steps, list):
                print(f'  TC {i}: steps is LIST with {len(steps)} items')
            else:
                print(f'  TC {i}: steps is {type(steps).__name__}')
except json.JSONDecodeError as e:
    print('JSON PARSE FAILED:', e)

# Now validate
try:
    result = TestCaseListResponse.model_validate_json(content)
    print('VALIDATION: OK')
    print(result.model_dump_json(indent=2))
except Exception as e:
    print('VALIDATION FAILED:', e)
