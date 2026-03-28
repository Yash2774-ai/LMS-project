import os

def fix_moodle_utils():
    path = 'frontend/moodle_utils.py'
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()

    # Remove the old aliases at the top
    text = text.replace('MOODLE_URL = settings.MOODLE_API_URL\n', '')
    text = text.replace('MOODLE_URL = settings.MOODLE_URL\n', '')
    text = text.replace('MOODLE_TOKEN = settings.MOODLE_TOKEN\n', '')

    # Replace all usage
    text = text.replace('MOODLE_URL', 'settings.MOODLE_URL')
    text = text.replace('MOODLE_TOKEN', 'settings.MOODLE_TOKEN')

    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
        
def fix_views():
    path = 'frontend/views.py'
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()

    text = text.replace(
        'from .moodle_utils import MOODLE_URL\n        moodle_base = MOODLE_URL.replace', 
        'from django.conf import settings\n        moodle_base = settings.MOODLE_URL.replace'
    )
    text = text.replace(
        'from adaptive_lms.settings import MOODLE_URL, MOODLE_TOKEN', 
        'from django.conf import settings'
    )
    text = text.replace('requests.post(MOODLE_URL', 'requests.post(settings.MOODLE_URL')
    text = text.replace("'wstoken': MOODLE_TOKEN", "'wstoken': settings.MOODLE_TOKEN")

    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)

if __name__ == "__main__":
    fix_moodle_utils()
    fix_views()
    print("Files Updated!")
