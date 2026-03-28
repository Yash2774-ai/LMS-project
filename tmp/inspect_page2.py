import os, sys, django

sys.path.insert(0, r'c:\Yash\Placement\8th Sem Internship\adaptive_learning_lms')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'adaptive_lms.settings')
django.setup()

import requests
import json
from django.conf import settings

log_path = r'c:\Yash\Placement\8th Sem Internship\adaptive_learning_lms\tmp\page_inspect.log'

with open(log_path, 'w', encoding='utf-8') as f:

    # 1. Fetch the raw index.html
    url = 'http://localhost/moodle/webservice/pluginfile.php/90/mod_page/content/index.html'
    params = {'forcedownload': 1, 'token': settings.MOODLE_TOKEN}
    resp = requests.get(url, params=params, timeout=10)
    f.write(f"=== index.html Fetch ===\n")
    f.write(f"Status: {resp.status_code}\n")
    f.write(f"Content-Type: {resp.headers.get('Content-Type')}\n")
    f.write(f"Content:\n{resp.text}\n\n")

    # 2. Use mod_page_get_pages_by_courses
    from frontend.moodle_utils import moodle_api_call
    result = moodle_api_call('mod_page_get_pages_by_courses', {'courseids[0]': 2})
    f.write("=== mod_page_get_pages_by_courses ===\n")
    f.write(json.dumps(result, indent=2))

print(f"Done! Check: {log_path}")
