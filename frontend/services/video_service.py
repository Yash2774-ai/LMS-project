import os
import requests
import subprocess
from django.conf import settings
from frontend.models import Module, StudentModuleProgress
from frontend.moodle_utils import get_course_contents, extract_video_url_from_module

def process_moodle_video(cmid):
    """
    Automated job to extract video from Moodle module (resource/page/etc) and convert to DASH.
    Returns the new mpd_url if successful, else None.
    """
    try:
        module_instance = Module.objects.get(moodle_cmid=cmid)
        course_id = module_instance.moodle_course_id
        
        print(f"[VideoService] Processing CMID {cmid} for course {course_id}")
        
        # 1. Fetch Course Contents to find the specific module
        sections = get_course_contents(course_id)
        if not sections:
            print(f"[VideoService] Failed to fetch contents for course {course_id}")
            return None
            
        target_module = None
        for section in sections:
            for mod in section.get('modules', []):
                if mod.get('id') == cmid:
                    target_module = mod
                    break
            if target_module:
                break
        
        if not target_module:
            print(f"[VideoService] Module with CMID {cmid} not found in Moodle course {course_id}")
            return None
            
        # 2. Extract Video URL from the module data
        video_url = extract_video_url_from_module(target_module)
        if not video_url:
            print(f"[VideoService] No video URL found for CMID {cmid} (Type: {target_module.get('modname')})")
            return None
            
        print(f"[VideoService] Extracted Video URL: {video_url}")
        
        # 3. Download Video
        raw_dir = os.path.join(settings.MEDIA_ROOT, 'raw_videos')
        os.makedirs(raw_dir, exist_ok=True)
        local_filename = f"moodle_{cmid}.mp4"
        raw_path = os.path.join(raw_dir, local_filename)
        
        print(f"[VideoService] Downloading to {raw_path}...")
        response = requests.get(video_url, stream=True)
        if response.status_code == 200:
            with open(raw_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
        else:
            print(f"[VideoService] Download failed: Status {response.status_code}")
            return None
            
        # 4. Convert to DASH
        local_module_id = module_instance.id
        # Use lesson_{cmid} to match build_dash_stream_url logic in moodle_utils.py
        dash_dir = os.path.join(settings.MEDIA_ROOT, 'dash', f'lesson_{cmid}')
        os.makedirs(dash_dir, exist_ok=True)
        dash_output = os.path.join(dash_dir, 'manifest.mpd').replace("\\", "/")
        
        # Use simple but effective DASH command
        cmd = [
            "ffmpeg", "-y", "-i", raw_path.replace("\\", "/"),
            "-map", "0:v", "-map", "0:a",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-g", "48", "-keyint_min", "48", "-sc_threshold", "0",
            "-use_timeline", "1", "-use_template", "1", "-f", "dash",
            dash_output
        ]
        
        print(f"[VideoService] Running FFmpeg for CMID {cmid}")
        subprocess.run(cmd, check=True, capture_output=True)
        
        # 5. Update Database
        # build_dash_stream_url expects /media/dash/lesson_{cmid}/manifest.mpd
        new_mpd_url = f"/media/dash/lesson_{cmid}/manifest.mpd"
        module_instance.mpd_url = new_mpd_url
        module_instance.save()
        
        # Reset progress for all students for this module because media content changed
        StudentModuleProgress.objects.filter(moodle_cmid=cmid).update(
            watch_percent=0,
            is_completed=False,
            last_position_seconds=0
        )
        
        print(f"[VideoService] SUCCESS: Video processed for CMID {cmid}")
        return new_mpd_url
        
    except Exception as e:
        import traceback
        print(f"[VideoService] ERROR for CMID {cmid}: {str(e)}")
        print(traceback.format_exc())
        return None
