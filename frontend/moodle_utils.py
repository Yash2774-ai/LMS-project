import requests
import re
from django.conf import settings

# For backward compatibility, expose as module-level constants
MOODLE_URL = getattr(settings, 'MOODLE_URL', None)
MOODLE_TOKEN = getattr(settings, 'MOODLE_TOKEN', None)


def moodle_api_call(function_name, params=None):
    """
    Generic wrapper for Moodle REST API calls.
    
    Args:
        function_name (str): The Moodle webservice function name (e.g., 'core_course_get_contents')
        params (dict): Optional dictionary of parameters to send to the API
    
    Returns:
        dict or list: The JSON response from Moodle, or empty dict/list on error
    """
    if params is None:
        params = {}

    base_params = {
        "wstoken": settings.MOODLE_TOKEN,
        "wsfunction": function_name,
        "moodlewsrestformat": "json"
    }

    base_params.update(params)

    try:
        response = requests.post(
            settings.MOODLE_URL,
            data=base_params
        )
        response.raise_for_status()
        data = response.json()
        
        if isinstance(data, dict) and data.get('exception'):
            print(f"Moodle API Error: {data}")
            return None
        return data
    except requests.exceptions.RequestException as e:
        print(f"Moodle API Connection Error ({function_name}): {e}")
        return None


def fetch_moodle_courses():
    """
    Fetches the list of courses from Moodle using the REST API.
    """
    print(f"DEBUG: Fetching courses from {settings.MOODLE_URL} using token {settings.MOODLE_TOKEN[:5]}...")
    params = {
        'wstoken': settings.MOODLE_TOKEN,
        'wsfunction': 'core_course_get_courses',
        'moodlewsrestformat': 'json',
    }
    
    try:
        response = requests.get(settings.MOODLE_URL, params=params)
        response.raise_for_status() 
        data = response.json()
        
        # Moodle returns a list on success, but a dict on error
        if isinstance(data, list):
            return data
        else:
            print(f"Moodle API Error: {data}")
            return []
            
    except requests.exceptions.RequestException as e:
        print(f"Connection Error: {e}")
        return []

def create_moodle_course(fullname, shortname, categoryid, description):
    print(f"DEBUG: Creating course {fullname} in category {categoryid}...")

    payload = {
        'wstoken': settings.MOODLE_TOKEN,
        'wsfunction': 'core_course_create_courses',
        'moodlewsrestformat': 'json',
        'courses[0][fullname]': fullname,
        'courses[0][shortname]': shortname,
        'courses[0][categoryid]': categoryid,
        'courses[0][summary]': description,
        'courses[0][format]': 'topics',
    }

    try:
        response = requests.post(settings.MOODLE_URL, data=payload)
        response.raise_for_status()

        result = response.json()
        print("Moodle Response:", result)   # 🔥 ADD THIS DEBUG

        if isinstance(result, list) and len(result) > 0:
            return {'success': True, 'course': result[0]}
        elif isinstance(result, dict) and 'exception' in result:
            return {'success': False, 'error': result.get('message')}
        else:
            return {'success': False, 'error': 'Unknown Moodle error'}

    except requests.exceptions.RequestException as e:
        print("Connection Error:", e)
        return {'success': False, 'error': str(e)}

def get_course_contents(course_id):
    """
    Fetches course contents (sections and modules) from Moodle.
    """
    params = {
        'wstoken': settings.MOODLE_TOKEN,
        'wsfunction': 'core_course_get_contents',
        'moodlewsrestformat': 'json',
        'courseid': course_id,
    }
    try:
        response = requests.get(settings.MOODLE_URL, params=params)
        response.raise_for_status()
        data = response.json()
        
        # Parse sections and add module_count
        if isinstance(data, list):
            modules = []
            for section in data:
                # Count number of modules in this section
                section_modules = section.get('modules', [])
                for module in section_modules:
                    modules.append(module)
                section['module_count'] = len(section_modules)
            print("Fetched modules from Moodle:", modules)
            return data
        return []
        
    except Exception as e:
        print(f"Error fetching course contents: {e}")
        return []

def get_enrolled_users(course_id):
    """
    Fetches list of enrolled users for a course.
    """
    params = {
        'wstoken': settings.MOODLE_TOKEN,
        'wsfunction': 'core_enrol_get_enrolled_users',
        'moodlewsrestformat': 'json',
        'courseid': course_id,
    }
    try:
        response = requests.get(settings.MOODLE_URL, params=params)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching enrolled users: {e}")
        return []

def update_course_visibility(course_id, visible):
    """
    Updates the visibility (hide/show) of a course.
    visible: 1 for show, 0 for hide
    """
    params = {
        'wstoken': settings.MOODLE_TOKEN,
        'wsfunction': 'core_course_update_courses',
        'moodlewsrestformat': 'json',
    }
    data = {
        'courses[0][id]': course_id,
        'courses[0][visible]': visible,
    }
    try:
        response = requests.post(settings.MOODLE_URL, params=params, data=data)
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"Error updating course visibility: {e}")
        return False

def delete_moodle_courses(course_ids):
    """
    Deletes courses from Moodle. course_ids should be a list.
    """
    params = {
        'wstoken': settings.MOODLE_TOKEN,
        'wsfunction': 'core_course_delete_courses',
        'moodlewsrestformat': 'json',
    }
    data = {}
    for i, cid in enumerate(course_ids):
        data[f'courseids[{i}]'] = cid
        
    try:
        response = requests.post(settings.MOODLE_URL, params=params, data=data)
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"Error deleting courses: {e}")
        return False

def add_moodle_module(course_id, section_number, module_type, module_data):

    payload = {
        'wstoken': settings.MOODLE_TOKEN,
        'wsfunction': 'local_teacherpanel_add_module',
        'moodlewsrestformat': 'json',

        'courseid': course_id,
        'sectionnum': section_number,
        'modulename': module_type,
        'name': module_data.get('title'),
        'intro': module_data.get('description', ''),
        'introformat': 1,
    }

    # Extra fields can be handled inside the Moodle custom plugin if needed,
    # but the current Django payload should be standardized for the custom WS.
    try:
        print(f"DEBUG ADD MODULE: Token used: {settings.MOODLE_TOKEN[:5]}...")
        print("DEBUG ADD MODULE: Function: local_teacherpanel_add_module")
        print(f"DEBUG ADD MODULE: Payload: {payload}")
        print("SECTION SENT TO MOODLE:", section_number)

        response = requests.post(settings.MOODLE_URL, data=payload)
        response.raise_for_status()

        result = response.json()
        print("MOODLE RAW RESPONSE:", result)

        if not isinstance(result, (dict, list)):
            return {'success': False, 'error': 'Invalid response format from Moodle', 'data': None}

        if isinstance(result, dict) and 'exception' in result:
            return {'success': False, 'error': result.get('message', result.get('exception')), 'data': None}

        return {'success': True, 'data': result, 'error': None}

    except Exception as e:
        return {'success': False, 'error': str(e), 'data': None}


def delete_moodle_module(cmid):
    """
    Deletes a course module from Moodle by its course module id (cmid).
    """
    payload = {
        'wstoken': settings.MOODLE_TOKEN,
        'wsfunction': 'core_course_delete_modules',
        'moodlewsrestformat': 'json',
        'cmids[0]': cmid,
    }
    try:
        print(f"DEBUG DELETE MODULE: cmid={cmid}")
        response = requests.post(settings.MOODLE_URL, data=payload)
        response.raise_for_status()
        result = response.json()
        print("DELETE MODULE RESPONSE:", result)
        # core_course_delete_modules returns null on success
        if result is None:
            return {'success': True}
        if isinstance(result, dict) and 'exception' in result:
            return {'success': False, 'error': result.get('message', result.get('exception'))}
        return {'success': True}
    except Exception as e:
        print("DELETE MODULE EXCEPTION:", str(e))
        return {'success': False, 'error': str(e)}


def get_quiz_attempts(quiz_id, user_id=None):
    """Fetch quiz attempts for a specific quiz and optionally a specific user."""
    params = {'quizid': quiz_id}
    if user_id:
        params['userid'] = user_id
    return moodle_api_call("mod_quiz_get_user_attempts", params)

def get_activities_completion(course_id, user_id):
    """Fetch activity completion status for a user in a course."""
    return moodle_api_call(
        "core_completion_get_activities_completion_status",
        {
            "courseid": course_id,
            "userid": user_id
        }
    )

def build_dash_stream_url(moodle_cmid):
    """
    Build DASH streaming URL for a given Moodle course module ID (moodle_cmid).
    
    Args:
        moodle_cmid: The Moodle course module ID (NOT Django's auto id)
    
    Returns:
        str: URL to the DASH manifest (/media/dash/module_{moodle_cmid}/stream.mpd) if exists
        None: If file doesn't exist or MEDIA_ROOT not configured
    
    Folder naming convention:
        /media/dash/module_<moodle_cmid>/stream.mpd
        
    Debug:
        - Logs when DASH file exists ✓
        - Logs when it's missing ✗
    """
    import os
    import logging
    from django.conf import settings
    
    logger = logging.getLogger(__name__)
    
    if not moodle_cmid:
        logger.warning(f"[DASH_URL] build_dash_stream_url called with None/empty moodle_cmid")
        return None
    
    # Correct folder structure: /media/dash/module_<moodle_cmid>/stream.mpd
    rel_path = f"dash/module_{moodle_cmid}/stream.mpd"
    
    if not hasattr(settings, 'MEDIA_ROOT') or not settings.MEDIA_ROOT:
        logger.error(f"[DASH_URL] MEDIA_ROOT not configured")
        return None
    
    abs_path = os.path.join(settings.MEDIA_ROOT, rel_path)
    
    if os.path.exists(abs_path):
        url = f"/media/{rel_path}"
        logger.info(f"[DASH_URL] ✓ DASH file found for moodle_cmid={moodle_cmid}: {url}")
        return url
    else:
        logger.warning(f"[DASH_URL] ✗ DASH file NOT found for moodle_cmid={moodle_cmid}. Expected: {abs_path}")
        return None

def get_moodle_page_content(course_id):
    return moodle_api_call('mod_page_get_pages_by_courses', {'courseids[0]': course_id})

def extract_video_url_from_module(module):
    """
    Extracts a video URL from a Moodle module dictionary.
    Works for 'resource' modules (direct files) and 'page' modules (HTML content).
    """
    # 1. Check for resource modules (direct file attachments)
    if 'contents' in module:
        for content in module['contents']:
            if content.get('type') == 'file' and content.get('filename', '').lower().endswith('.mp4'):
                url = content.get('fileurl')
                if url:
                    # Append token if necessary
                    if '/webservice/pluginfile.php' in url and 'token=' not in url:
                        from django.conf import settings
                        sep = '&' if '?' in url else '?'
                        url = f"{url}{sep}token={settings.MOODLE_TOKEN}"
                    return url

    # 2. Check for page modules specifically
    if module.get('modname') == 'page' and 'contents' in module:
        for content in module['contents']:
            if content.get('filename') == 'index.html':
                fileurl = content.get('fileurl')
                if fileurl:
                    from django.conf import settings
                    if 'token=' not in fileurl:
                        sep = '&' if '?' in fileurl else '?'
                        fileurl = f"{fileurl}{sep}token={settings.MOODLE_TOKEN}"
                    try:
                        import requests
                        resp = requests.get(fileurl, timeout=10)
                        if resp.status_code == 200:
                            video_url = extract_video_url_from_html(resp.text)
                            if video_url:
                                return video_url
                    except Exception as e:
                        print(f"DEBUG: Failed to fetch index.html for page module: {e}")

    # 3. Check for page/label modules (HTML content in description or intro)
    html_content = module.get('description', '') or module.get('intro', '')
    if html_content:
        return extract_video_url_from_html(html_content)
    
    return None

def extract_video_url_from_html(html_content):
    if not html_content:
        return None
    import re
    # Look for common video patterns
    patterns = [
        r'<source[^>]+src=["\']([^"\']+\.mp4[^"\']*)["\']',
        r'<video[^>]+src=["\']([^"\']+\.mp4[^"\']*)["\']',
        r'href=["\']([^"\']+\.mp4[^"\']*)["\']',
        r'src=["\']([^"\']+\.mp4[^"\']*)["\']',
        # Handle encoded URLs or pluginfile URLs without extensions
        r'pluginfile\.php/[^"\']+/content/[^"\']+',
    ]
    for pattern in patterns:
        match = re.search(pattern, html_content, re.IGNORECASE)
        if match:
            url = match.group(1) if '(' not in pattern else match.group(0)
            # If it's a relative URL or path, we might need more logic, 
            # but for Moodle it's usually absolute or pluginfile.
            if '/webservice/pluginfile.php' in url and 'token=' not in url:
                from django.conf import settings
                sep = '&' if '?' in url else '?'
                url = f"{url}{sep}token={settings.MOODLE_TOKEN}"
            return url
    return None
