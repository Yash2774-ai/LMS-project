import os, sys, django

sys.path.insert(0, r'c:\Yash\Placement\8th Sem Internship\adaptive_learning_lms')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'adaptive_lms.settings')
django.setup()

import requests
from django.conf import settings

# The fileurl for index.html comes from get_course_contents for CMID 74
url = 'http://localhost/moodle/webservice/pluginfile.php/90/mod_page/content/index.html'
params = {'forcedownload': 1, 'token': settings.MOODLE_TOKEN}

resp = requests.get(url, params=params, timeout=10)
print(f"Status: {resp.status_code}")
print(f"Content-Type: {resp.headers.get('Content-Type')}")
print(f"Content Length: {len(resp.text)}")
print("=== CONTENT ===")
print(resp.text[:3000])
print("=== END ===")

# Also try using mod_page_get_pages_by_courses
from frontend.moodle_utils import moodle_api_call
result = moodle_api_call('mod_page_get_pages_by_courses', {'courseids[0]': 2})
print("\n=== mod_page_get_pages_by_courses RESULT ===")
if result and isinstance(result, dict):
    pages = result.get('pages', [])
    for page in pages:
        print(f"  Page ID: {page.get('id')}, CMID: {page.get('coursemodule')}, Name: {page.get('name')}")
        content = page.get('content', '')
        if content:
            print(f"  Content (first 500 chars): {content[:500]}")
        else:
            print("  No content in API response")
