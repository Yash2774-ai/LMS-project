import sys
import os
import django

sys.path.append(r"c:\Yash\Placement\8th Sem Internship\adaptive_learning_lms")
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'adaptive_lms.settings')
django.setup()

from frontend.moodle_utils import moodle_api_call
# Try to get course module
res = moodle_api_call('core_course_get_course_module', {'cmid': 2})
print(res)
