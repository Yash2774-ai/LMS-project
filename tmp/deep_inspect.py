import os, sys, django

sys.path.insert(0, r'c:\Yash\Placement\8th Sem Internship\adaptive_learning_lms')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'adaptive_lms.settings')
django.setup()

import requests
import json
from django.conf import settings

log_path = r'c:\Yash\Placement\8th Sem Internship\adaptive_learning_lms\tmp\page_inspect2.log'

with open(log_path, 'w', encoding='utf-8') as f:
    f.write(f"MOODLE_URL: {settings.MOODLE_URL}\n")
    f.write(f"MOODLE_TOKEN (first 10): {settings.MOODLE_TOKEN[:10]}\n\n")

    # Try fetching page content via core_course_get_contents with options
    data = {
        'wstoken': settings.MOODLE_TOKEN,
        'wsfunction': 'core_course_get_contents',
        'moodlewsrestformat': 'json',
        'courseid': 2,
        'options[0][name]': 'modname',
        'options[0][value]': 'page',
    }
    resp = requests.post(settings.MOODLE_URL, data=data)
    f.write("=== core_course_get_contents (filter page) ===\n")
    result = resp.json()
    # Find module 74
    for section in result:
        for mod in section.get('modules', []):
            if mod.get('id') == 74:
                f.write(f"Found module 74:\n")
                f.write(json.dumps(mod, indent=2))
                f.write("\n\n")

    # Try mod_page_get_pages_by_courses differently - per cmid
    data2 = {
        'wstoken': settings.MOODLE_TOKEN,
        'wsfunction': 'core_course_get_contents',
        'moodlewsrestformat': 'json', 
        'courseid': 2,
    }
    resp2 = requests.post(settings.MOODLE_URL, data=data2)
    sections = resp2.json()
    f.write("=== All module details from core_course_get_contents ===\n")
    for sec in sections:
        for mod in sec.get('modules', []):
            if mod.get('id') == 74:
                f.write("FULL MOD 74 RAW:\n")
                f.write(json.dumps(mod, indent=2))
                f.write("\n")

    # Also attempt to use mod_page_get_pages_by_courses with just the cmid approach
    data3 = {
        'wstoken': settings.MOODLE_TOKEN,
        'wsfunction': 'mod_page_get_pages_by_courses',
        'moodlewsrestformat': 'json',
        'courseids[0]': 2,
    }
    resp3 = requests.post(settings.MOODLE_URL, data=data3)
    f.write("\n=== mod_page_get_pages_by_courses raw response ===\n")
    f.write(repr(resp3.text[:2000]))

print(f"Done. Check: {log_path}")
