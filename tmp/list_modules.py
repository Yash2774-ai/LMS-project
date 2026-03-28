import os
import django
import sys

# Add the project directory to sys.path
sys.path.append(r'c:\Yash\Placement\8th Sem Internship\adaptive_learning_lms')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'adaptive_lms.settings')
django.setup()

from frontend.moodle_utils import get_course_contents
import json

def run():
    try:
        res = get_course_contents(2)
        print("--- MODULES FOR COURSE 2 ---")
        for section in res:
            print(f"\nSection: {section.get('name')}")
            for m in section.get('modules', []):
                print(f"  ID: {m.get('id')} | Name: {m.get('name')} | Type: {m.get('modname')}")
                # Check contents for files
                filenames = [c.get('filename') for c in m.get('contents', [])]
                if filenames:
                    print(f"    Contents: {filenames}")
                # Check description/intro for videos
                intro = m.get('description') or m.get('intro')
                if intro and ('<video' in intro or '<iframe' in intro or '.mp4' in intro):
                    print(f"    Possible Video in HTML: Yes")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == '__main__':
    run()
