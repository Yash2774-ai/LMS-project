from adaptive_lms.moodle_utils import add_moodle_module

print(add_moodle_module(
    5,
    1,
    "page",
    {
        "title": "Shell Test",
        "description": "Testing",
        "content": "<p>Test</p>"
    }
))