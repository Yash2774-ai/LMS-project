from frontend.models import ActivityLog

def log_activity(user_name, action, course_name=None, status="Success"):
    ActivityLog.objects.create(
        user_name=user_name,
        action=action,
        course_name=course_name,
        status=status,
    )
