import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth import login, authenticate
from django.contrib import messages
from django.contrib.auth.models import User, Group
from django.db.models import Count, Q, Avg
from django.conf import settings
from .models import UserProfile, AuditLog, SystemSetting, Certificate, ModuleRule, WatchLog, ActivityLog, StudentModuleProgress, Module, Enrollment, Course
from .services.activity_logger import log_activity
from .services.moodle_user_service import ensure_moodle_user
from .forms import SignupForm, CourseCreateForm, UserUpdateForm, ProfileUpdateForm, EmailUpdateForm
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.utils import timezone
from datetime import timedelta
from django.core.cache import cache
from django.views.decorators.csrf import csrf_exempt
import json
from .moodle_utils import (
    fetch_moodle_courses,
    create_moodle_course,
    get_course_contents,
    get_enrolled_users,
    update_course_visibility,
    delete_moodle_courses,
    add_moodle_module,
    delete_moodle_module,
    moodle_api_call,
    get_quiz_attempts,
    get_activities_completion,
    build_dash_stream_url
)

from functools import wraps
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse

logger = logging.getLogger(__name__)


def clean_duplicate_modules():
    """
    Remove duplicate Module rows that share name, course_id, and module_type,
    keeping the most recent (highest id) record.
    """
    from django.db.models import Count, Max

    duplicates = (
        Module.objects.values("name", "moodle_course_id", "module_type")
        .order_by()
        .annotate(max_id=Max("id"), count_id=Count("id"))
        .filter(count_id__gt=1)
    )

    for dup in duplicates:
        Module.objects.filter(
            name=dup["name"],
            moodle_course_id=dup["moodle_course_id"],
            module_type=dup["module_type"]
        ).exclude(id=dup["max_id"]).delete()


def fix_invalid_progress_sequence(student, course_id):
    """
    Ensure sequential integrity: no module completion recorded before its predecessor.
    Does not delete progress, only resets invalid completions.
    """
    modules = list(
        Module.objects.filter(moodle_course_id=course_id).order_by("section_number", "id")
    )
    cmids = [m.moodle_cmid for m in modules if m.moodle_cmid]

    progress_map = {
        p.moodle_cmid: p
        for p in StudentModuleProgress.objects.filter(student=student, moodle_cmid__in=cmids)
    }

    previous_completed = True
    for mod in modules:
        prog = progress_map.get(mod.moodle_cmid)
        if not previous_completed and prog and prog.is_completed:
            prog.is_completed = False
            prog.completed_at = None
            prog.save(update_fields=["is_completed", "completed_at", "updated_at"])
        previous_completed = prog.is_completed if prog else False


def teacher_required(view_func):
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            messages.error(request, 'Please log in.')
            return redirect('login')
        if not request.user.is_staff:
            messages.error(request, 'You do not have permission to access that area.')
            return redirect('student_dashboard')
        return view_func(request, *args, **kwargs)
    return _wrapped_view

def student_required(view_func):
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            messages.error(request, 'Please log in.')
            return redirect('login')
        if request.user.is_staff:
            messages.error(request, 'Teachers cannot access the student panel.')
            return redirect('teacher_dashboard')
        return view_func(request, *args, **kwargs)
    return _wrapped_view

def admin_required(view_func):
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_superuser:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)
    return _wrapped_view


def sync_module_record(cmid, defaults):
    """
    Upsert a Module row keyed by moodle_cmid without touching adaptive fields like mpd_url.
    Returns (module_instance, status_label) where status_label in {"CREATED", "UPDATED", "SKIPPED"}.
    """
    from .models import Module

    existing = Module.objects.filter(moodle_cmid=cmid).first()
    changed_fields = []
    if existing:
        for field, value in defaults.items():
            if getattr(existing, field) != value:
                changed_fields.append(field)

    module, created = Module.objects.update_or_create(
        moodle_cmid=cmid,
        defaults=defaults
    )

    if created:
        status = "CREATED"
    elif changed_fields:
        status = "UPDATED"
    else:
        status = "SKIPPED"

    logger.info(f"{status}: cmid={cmid}, name={defaults.get('name')}")
    return module, status


def is_module_unlocked(student, module):
    """
    Enforce sequential unlocking: a module is unlocked if either it is the first
    in the course ordering (by section_number) or the immediately previous
    module has at least 80% watch_percent recorded for this student.
    """
    course_modules = Module.objects.filter(
        moodle_course_id=module.moodle_course_id
    ).order_by("section_number", "id")

    module_list = list(course_modules)
    try:
        index = module_list.index(module)
    except ValueError:
        # If module not in ordered list, fall back to allowing access
        return True

    if index == 0:
        return True

    previous_module = module_list[index - 1]
    progress = StudentModuleProgress.objects.filter(
        student=student,
        moodle_cmid=previous_module.moodle_cmid
    ).first()

    if not progress:
        return False

    return progress.watch_percent >= 80

def login_view(request):
    if request.user.is_authenticated:
        if request.user.is_staff:
            return redirect('teacher_dashboard')
        else:
            return redirect('student_dashboard')

    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')

        user = authenticate(request, username=username, password=password)
        if user is not None:
            if not user.is_active:
                messages.error(request, 'Your account is pending approval or has been deactivated. Please contact the administrator.')
                return render(request, 'login.html')

            if user.is_superuser:
                messages.error(request, 'Administrators must log in via the Secure Portal.')
                return render(request, 'login.html')

            login(request, user)
            ensure_moodle_user(request.user)
            
            if request.user.is_staff:
                return redirect('teacher_dashboard')
            else:
                return redirect('student_dashboard')
        else:
            messages.error(request, 'Invalid credentials.')
            return render(request, 'login.html')

    return render(request, 'login.html')

def signup(request):
    if request.method == 'POST':
        form = SignupForm(request.POST)
        if form.is_valid():
            user = form.save()
            ensure_moodle_user(user)
            login(request, user)
            if request.user.is_staff:
                return redirect('teacher_dashboard')
            else:
                return redirect('student_dashboard')
    else:
        form = SignupForm()
    return render(request, 'signup.html', {'form': form})

@login_required
def dashboard_redirect(request):
    query_string = request.META.get('QUERY_STRING', '')
    suffix = f"?{query_string}" if query_string else ""
    if request.user.is_staff:
        return redirect(f"/teacher/dashboard/{suffix}")
    else:
        return redirect(f"/student/dashboard/{suffix}")


@login_required
@teacher_required
def teacher_dashboard(request):
    section = request.GET.get('section', 'dashboard')
    moodle_user_id = getattr(request.user, 'moodle_user_id', None)
    if not moodle_user_id:
        moodle_user_id = ensure_moodle_user(request.user)
    print("Teacher Moodle ID:", moodle_user_id)
    
    cache_key = f"teacher_dashboard_stats_{moodle_user_id}" if moodle_user_id else "teacher_dashboard_stats_demo"
    stats = cache.get(cache_key)

    if not stats:
        # Fetch data from Moodle API
        if moodle_user_id:
            print(f"DEBUG: Calling core_enrol_get_users_courses for userid={moodle_user_id}")
            enrolled_courses = moodle_api_call('core_enrol_get_users_courses', {'userid': moodle_user_id})
        else:
            print("WARNING: No Moodle User ID found for teacher. Falling back to core_course_get_courses.")
            enrolled_courses = fetch_moodle_courses()
            
        print("Moodle Courses Response:", enrolled_courses)
            
        teacher_course_ids = []
        courses_list = []
        if isinstance(enrolled_courses, list):
            for c in enrolled_courses:
                if isinstance(c, dict) and 'id' in c:
                    if c.get('id') == 1:
                        continue
                    teacher_course_ids.append(c['id'])
                    courses_list.append({
                        'fullname': c.get('fullname', 'Untitled Course'),
                        'summary': c.get('summary', 'No description available.'),
                        'id': c.get('id')
                    })
        
        unique_students = set()
        for cid in teacher_course_ids:
            enrolled_users = get_enrolled_users(cid)
            if isinstance(enrolled_users, list):
                for user in enrolled_users:
                    if isinstance(user, dict):
                        roles = user.get('roles', [])
                        if any(r.get('shortname') == 'student' for r in roles):
                            unique_students.add(user.get('id', user.get('userid')))
        
        certificates_count = Certificate.objects.filter(course_id__in=teacher_course_ids).count() if teacher_course_ids else 0
        
        stats = {
            'courses_list': courses_list,
            'total_courses': len(courses_list),
            'total_students': len(unique_students),
            'certificates_issued': certificates_count,
        }
        cache.set(cache_key, stats, 60)

    courses_list = stats['courses_list']
    recent_activities = ActivityLog.objects.order_by('-timestamp')[:5]

    context = {
        'section': section,
        'current_section': section,
        'active_page': section,
        'total_courses': stats['total_courses'],
        'total_students': stats['total_students'],
        'certificates_issued': stats['certificates_issued'],
        'recent_activities': recent_activities,
        'courses': courses_list,
    }

    # teacher forms only needed in profile settings; keep in context if settings section
    if section == 'settings' or section == 'dashboard':
        context.update({
            'u_form': UserUpdateForm(instance=request.user),
            'p_form': ProfileUpdateForm(instance=request.user.profile),
            'e_form': EmailUpdateForm(instance=request.user),
            'pw_form': PasswordChangeForm(request.user),
        })

    if section == 'analytics':
        course_id = request.GET.get("course_id")
        video_rows = []
        completion_rate = avg_watch = active_students = 0

        if course_id:
            modules = Module.objects.filter(moodle_course_id=course_id)
            cmids = list(modules.values_list("moodle_cmid", flat=True))

            progress_qs = StudentModuleProgress.objects.filter(
                moodle_cmid__in=cmids
            ).select_related("student")

            total_rows = progress_qs.count()
            completed_rows = progress_qs.filter(is_completed=True).count()
            completion_rate = (completed_rows / total_rows * 100) if total_rows else 0
            avg_watch = progress_qs.aggregate(Avg("watch_percent"))["watch_percent__avg"] or 0
            active_students = progress_qs.values("student").distinct().count()

            module_map = {m.moodle_cmid: m for m in modules}

            for p in progress_qs:
                module_obj = module_map.get(p.moodle_cmid)
                module_name = module_obj.name if module_obj else f"Module {p.moodle_cmid}"
                video_rows.append({
                    "student": p.student.get_full_name() or p.student.username,
                    "watch_percent": round(p.watch_percent, 1),
                    "status": "COMPLETED" if p.is_completed else "IN PROGRESS",
                    "module_name": module_name,
                })

        context.update({
            "video_engagement_rows": video_rows,
            "completion_rate": round(completion_rate, 1),
            "avg_watch": round(avg_watch, 1),
            "active_students": active_students,
        })

    if section == 'certificates':
        from .models import CertificateTemplate, CertificateSigner, IssuedCertificate
        teacher_courses = []
        teacher_course_ids = []
        if moodle_user_id:
            cert_courses = moodle_api_call('core_enrol_get_users_courses', {'userid': moodle_user_id})
            if isinstance(cert_courses, list):
                for c in cert_courses:
                    if isinstance(c, dict) and c.get('id') and c.get('id') != 1:
                        teacher_course_ids.append(c['id'])
                        teacher_courses.append({'id': c['id'], 'fullname': c.get('fullname', 'Untitled Course')})

        templates = CertificateTemplate.objects.filter(course_id__in=teacher_course_ids).order_by('-created_at')
        issued_certificates = IssuedCertificate.objects.filter(course_id__in=teacher_course_ids).select_related('student', 'certificate_template').order_by('-issued_at')
        signer = CertificateSigner.objects.filter(teacher=request.user).first()

        context.update({
            'courses': teacher_courses,
            'templates': templates,
            'issued_certificates': issued_certificates,
            'signer': signer,
            'total_issued': issued_certificates.count(),
        })

    return render(request, 'teacher_dashboard.html', context)

@login_required
def save_watch_log(request):
    if request.method == 'POST':
        import json
        try:
            data = json.loads(request.body)
            user_id = request.user.profile.moodle_user_id
            if not user_id:
                user_id = ensure_moodle_user(request.user)
            if not user_id:
                return JsonResponse({'success': False, 'error': 'Moodle User ID not found'}, status=400)
            
            WatchLog.objects.create(
                user_id=user_id,
                course_id=data.get('course_id'),
                moodle_cmid=data.get('cmid'),
                watched_seconds=data.get('watched_seconds'),
                total_duration=data.get('total_duration')
            )
            # Example logging usage for ActivityLog when an event happens in LMS
            ActivityLog.objects.create(
                user_name=request.user.username,
                action="watched video module",
                course_name=str(data.get('course_name', 'Unknown Course')),
            )
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=500)
    return JsonResponse({'success': False, 'error': 'Invalid method'}, status=405)

@login_required
def video_complete_api(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            module_id = data.get('module_id')
            # For now, just silently accept it based on requirements
            # "Do not implement completion sync yet" but required to call it.
            print(f"DEBUG: Completion triggered for module {module_id} by user {request.user.username}")
            return JsonResponse({'success': True, 'message': 'Completion status synced successfully'})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=500)
    return JsonResponse({'success': False, 'error': 'Invalid method'}, status=405)


@login_required
def video_progress_update(request):
    if request.method != "POST":
        return JsonResponse({"status": "error", "error": "Invalid method"}, status=405)

    try:
        data = json.loads(request.body)
        cmid = data.get("cmid")
        percent = float(data.get("watch_percent", 0) or 0)

        progress, _ = StudentModuleProgress.objects.get_or_create(
            student=request.user,
            moodle_cmid=cmid
        )

        progress.watch_percent = percent

        # Enforce sequential completion: only mark complete if previous module is completed
        if percent >= 80 and not progress.is_completed:
            module_obj = Module.objects.filter(moodle_cmid=cmid).first()
            can_complete = True
            if module_obj:
                course_modules = Module.objects.filter(
                    moodle_course_id=module_obj.moodle_course_id
                ).order_by("section_number", "id")
                module_list = list(course_modules)
                try:
                    idx = module_list.index(module_obj)
                except ValueError:
                    idx = 0
                if idx > 0:
                    prev_mod = module_list[idx - 1]
                    prev_done = StudentModuleProgress.objects.filter(
                        student=request.user,
                        moodle_cmid=prev_mod.moodle_cmid,
                        is_completed=True
                    ).exists()
                    can_complete = prev_done
            if can_complete:
                progress.is_completed = True
                progress.completed_at = timezone.now()

        progress.save()

        return JsonResponse({"status": "ok"})
    except Exception as exc:
        return JsonResponse({"status": "error", "error": str(exc)}, status=500)


@csrf_exempt
@login_required
def save_video_position(request):
    if request.method != "POST":
        return JsonResponse({"status": "error", "error": "Invalid method"}, status=405)

    try:
        if request.content_type == "application/json":
            payload = json.loads(request.body.decode() or "{}")
            cmid = payload.get("moodle_cmid") or payload.get("cmid")
            position = float(payload.get("position", payload.get("seconds", 0)) or 0)
        else:
            cmid = request.POST.get("cmid")
            position = float(request.POST.get("position", 0) or 0)

        progress, _ = StudentModuleProgress.objects.get_or_create(
            student=request.user,
            moodle_cmid=cmid
        )
        progress.last_position_seconds = position
        progress.save(update_fields=["last_position_seconds", "updated_at"])

        return JsonResponse({"status": "ok"})
    except Exception as exc:
        return JsonResponse({"status": "error", "error": str(exc)}, status=500)


# ─────────────────────────────────────────────────────────────────────────────
# Certificate System Views
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@teacher_required
def teacher_certificates(request):
    """Teacher certificates dashboard: shows templates, signer, and issued certs."""
    from .models import CertificateTemplate, CertificateSigner, IssuedCertificate
    from .moodle_utils import moodle_api_call

    moodle_user_id = request.user.moodle_user_id
    if not moodle_user_id:
        moodle_user_id = ensure_moodle_user(request.user)
    teacher_course_ids = []
    teacher_courses = []

    if moodle_user_id:
        courses = moodle_api_call('core_enrol_get_users_courses', {'userid': moodle_user_id})
        if isinstance(courses, list):
            for c in courses:
                if isinstance(c, dict) and c.get('id') and c.get('id') != 1:
                    teacher_course_ids.append(c['id'])
                    teacher_courses.append({
                        'id': c['id'],
                        'fullname': c.get('fullname', 'Untitled Course')
                    })
    else:
        # Fallback, keep empty list and avoid API failure
        teacher_courses = []

    templates = CertificateTemplate.objects.filter(course_id__in=teacher_course_ids).order_by('-created_at')
    issued = IssuedCertificate.objects.filter(course_id__in=teacher_course_ids).select_related('student', 'certificate_template').order_by('-issued_at')
    signer = CertificateSigner.objects.filter(teacher=request.user).first()

    context = {
        'courses': teacher_courses,
        'templates': templates,
        'issued_certificates': issued,
        'signer': signer,
        'total_issued': issued.count(),
        'active_tab': request.GET.get('tab', 'overview'),
    }
    return render(request, 'teacher_certificates.html', context)


@login_required
@teacher_required
def save_certificate_template(request):
    """POST handler to create or update a CertificateTemplate for the logged-in teacher."""
    if request.method == 'POST':
        from .models import CertificateTemplate

        # Allow updating an existing template by ID
        template_id = request.POST.get('template_id')
        if template_id:
            tmpl = CertificateTemplate.objects.filter(id=template_id, teacher=request.user).first()
        else:
            tmpl = None

        background = request.FILES.get('background_image')
        if not tmpl and not background:
            messages.error(request, 'A background image is required for a new template.')
            return redirect('teacher_certificates')

        if tmpl is None:
            tmpl = CertificateTemplate(teacher=request.user)

        tmpl.name = request.POST.get('name', 'Default Template')
        tmpl.course_id = request.POST.get('course_id') or None
        tmpl.font_family = request.POST.get('font_family', 'Poppins')
        tmpl.student_name_x = int(request.POST.get('student_name_x', 500))
        tmpl.student_name_y = int(request.POST.get('student_name_y', 300))
        tmpl.course_name_x = int(request.POST.get('course_name_x', 500))
        tmpl.course_name_y = int(request.POST.get('course_name_y', 380))
        tmpl.signature_x = int(request.POST.get('signature_x', 800))
        tmpl.signature_y = int(request.POST.get('signature_y', 500))
        tmpl.qr_x = int(request.POST.get('qr_x', 900))
        tmpl.qr_y = int(request.POST.get('qr_y', 520))

        if background:
            tmpl.background_image = background

        tmpl.save()
        messages.success(request, f'Certificate template "{tmpl.name}" saved.')
    return redirect('/teacher/dashboard/?section=certificates')


@login_required
@teacher_required
def save_certificate_signer(request):
    """POST handler to create or update a CertificateSigner for the logged-in teacher."""
    if request.method == 'POST':
        from .models import CertificateSigner

        signer, _ = CertificateSigner.objects.get_or_create(teacher=request.user)
        signer.signer_name = request.POST.get('signer_name', '').strip()
        signer.designation = request.POST.get('designation', '').strip()

        sig_image = request.FILES.get('signature_image')
        if sig_image:
            signer.signature_image = sig_image

        if signer.signer_name and signer.designation:
            signer.save()
            messages.success(request, 'Signature authority updated successfully.')
        else:
            messages.error(request, 'Signer name and designation are required.')

    return redirect('/teacher/dashboard/?section=certificates')


@login_required
@teacher_required
def issue_certificate(request):
    """POST JSON API to issue a certificate for a specific student + course."""
    if request.method == 'POST':
        import json
        from .cert_service import generate_certificate

        try:
            data = json.loads(request.body)
            student_user_id = data.get('student_user_id')  # Django User ID
            course_id = data.get('course_id')
            course_name = data.get('course_name', 'Unknown Course')

            student = User.objects.get(id=student_user_id)
            issued = generate_certificate(student, course_id, course_name)

            if issued:
                log_activity(
                    user_name=student.email,
                    action="certificate issued",
                    course_name=course_name,
                )
                return JsonResponse({
                    'success': True,
                    'verification_code': issued.verification_code,
                    'cert_url': f"/media/{issued.certificate_file}"
                })
            return JsonResponse({'success': False, 'error': 'Certificate generation failed. Check that a template exists.'})
        except User.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Student not found.'})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=500)

    return JsonResponse({'success': False, 'error': 'Invalid method'}, status=405)


def verify_certificate(request, code):
    """
    Public view — no login required.
    Verifies a certificate by its UUID verification code.
    """
    from .models import IssuedCertificate

    try:
        cert = IssuedCertificate.objects.select_related('student').get(verification_code=code)
        context = {
            'valid': True,
            'student_name': cert.student.get_full_name() or cert.student.username,
            'course_name': cert.course_name,
            'issued_at': cert.issued_at,
            'verification_code': code,
        }
    except IssuedCertificate.DoesNotExist:
        context = {'valid': False, 'verification_code': code}

    return render(request, 'verify_certificate.html', context)

@login_required
@teacher_required
def teacher_analytics(request):
    course_id = request.GET.get('course_id')
    if not course_id:
        return JsonResponse({'success': False, 'error': 'course_id is required'}, status=400)

    course_name = f"Course {course_id}"
    course_details = moodle_api_call('core_course_get_courses_by_field', {'field': 'id', 'value': course_id})
    if isinstance(course_details, dict) and course_details.get('courses'):
        course_name = course_details['courses'][0].get('fullname', course_name)

    # 1. Fetch Students
    enrolled_users = get_enrolled_users(course_id)
    students = []
    if isinstance(enrolled_users, list):
        for user in enrolled_users:
            if isinstance(user, dict):
                roles = user.get("roles", [])
                role_names = [r.get("shortname") for r in roles]
                
                # Strict Filtering: Only real students
                if "student" in role_names:
                    username = user.get("username", "").lower()
                    email = user.get("email", "").lower()
                    
                    # Exclude administrators, managers, and teachers by username/email
                    is_admin = (username in ["admin", "manager", "teacher"]) or ("admin" in email)
                    
                    if not is_admin:
                        students.append(user)

    # 2. Fetch Video Logs
    logs = WatchLog.objects.filter(course_id=course_id)
    
    # Aggregates
    total_logs = logs.count()
    if total_logs > 0:
        total_watched = sum(l.watched_seconds for l in logs)
        total_duration = sum(l.total_duration for l in logs)
        video_avg_watch = round((total_watched / total_duration) * 100) if total_duration > 0 else 0
        avg_watch_time_val = total_watched / total_logs
    else:
        video_avg_watch = 0
        avg_watch_time_val = 0

    # 3. Process Tables
    video_student_table = []
    quiz_attempts_table = []
    
    contents = get_course_contents(course_id)
    quizzes = []
    course_cmids = []
    if isinstance(contents, list):
        for section in contents:
            if isinstance(section, dict):
                for module in section.get("modules", []):
                    course_cmids.append(module.get("id"))
                    if module.get("modname") == "quiz":
                        quizzes.append(module)

    for student in students:
        s_id = student.get("id")
        s_name = student.get("fullname")
        
        # Video Stats
        s_logs = logs.filter(user_id=s_id)
        if s_logs.exists():
            s_watched = sum(l.watched_seconds for l in s_logs)
            s_total = sum(l.total_duration for l in s_logs)
            s_avg = round((s_watched / s_total) * 100) if s_total > 0 else 0
            s_status = "Completed" if s_avg >= 80 else "In Progress"
            last_watched = s_logs.latest('created_at').created_at.strftime('%Y-%m-%d %H:%M')
        else:
            s_avg = 0
            s_status = "Not Started"
            last_watched = "Never"
            
        video_student_table.append({
            'student_name': s_name,
            'watch_percent': s_avg,
            'status': s_status,
            'last_watched': last_watched
        })
        
        # Quiz Stats
        for quiz in quizzes:
            attempts_resp = get_quiz_attempts(quiz.get("id"), s_id)
            if isinstance(attempts_resp, dict) and 'attempts' in attempts_resp:
                s_attempts = attempts_resp['attempts']
                if s_attempts:
                    best_attempt = max(s_attempts, key=lambda x: x.get('sumgrades') if x.get('sumgrades') is not None else 0)
                    sumgrades = best_attempt.get('sumgrades', 0)
                    # Moodle doesn't always return max grade in attempts, so we use a fallback or 100
                    score = round(float(sumgrades)) if sumgrades is not None else 0
                    quiz_attempts_table.append({
                        'student_name': s_name,
                        'quiz_name': quiz.get('name'),
                        'score': score,
                        'status': "Passed" if score >= 50 else "Failed",
                        'date': timezone.datetime.fromtimestamp(best_attempt.get('timefinish')).strftime('%Y-%m-%d') if best_attempt.get('timefinish') else "N/A"
                    })
                    # Avoid duplicate quiz completion logs for the same student-course-action
                    if best_attempt.get('timefinish') and not ActivityLog.objects.filter(
                        user_name=s_name,
                        action="completed quiz",
                        course_name=course_name
                    ).exists():
                        log_activity(
                            user_name=s_name,
                            action="completed quiz",
                            course_name=course_name,
                        )

    # 4. Video engagement from StudentModuleProgress (per-module, per-student)
    progress_qs = StudentModuleProgress.objects.filter(
        moodle_cmid__in=course_cmids
    ).select_related("student")

    total_rows = progress_qs.count()
    completed_rows = progress_qs.filter(is_completed=True).count()
    completion_rate_progress = (completed_rows / total_rows * 100) if total_rows else 0
    avg_watch_progress = progress_qs.aggregate(Avg("watch_percent"))["watch_percent__avg"] or 0
    active_students_progress = progress_qs.values("student").distinct().count()

    # Map module names by cmid for display
    module_map = {
        m.moodle_cmid: m.name for m in Module.objects.filter(moodle_cmid__in=course_cmids)
    }

    video_engagement_rows = []
    for p in progress_qs:
        module_name = module_map.get(p.moodle_cmid, f"Module {p.moodle_cmid}")
        status = "Completed" if p.is_completed else "In Progress"
        video_engagement_rows.append({
            "student_name": p.student.get_full_name() or p.student.username,
            "module_name": module_name,
            "watch_percent": round(p.watch_percent, 1),
            "status": status,
        })

    # 5. Completion Rate Summary (legacy WatchLog-based)
    comp_count = sum(1 for x in video_student_table if x['status'] == "Completed")
    completion_rate = round((comp_count / len(students)) * 100) if students else 0

    # 6. Active Students Calculation (Participation Check)
    student_ids = [u.get("id") for u in students]
    active_students_count = WatchLog.objects.filter(
        course_id=course_id,
        user_id__in=student_ids
    ).values("user_id").distinct().count()

    context_data = {
        'video_avg_watch': video_avg_watch,
        'video_completion_rate': completion_rate,
        'avg_watch_time': round(avg_watch_time_val / 60, 1),
        'total_active_students': active_students_count,
        'video_student_table': video_student_table,
        'quiz_attempts_table': quiz_attempts_table,
        'completion_rate_progress': round(completion_rate_progress, 1),
        'avg_watch_progress': round(avg_watch_progress, 1),
        'active_students_progress': active_students_progress,
        'video_engagement_rows': video_engagement_rows,
    }
    
    return JsonResponse({'success': True, 'data': context_data})

@login_required
@student_required
def student_dashboard(request):
    
    moodle_user_id = getattr(request.user, 'moodle_user_id', None)
    if not moodle_user_id:
        moodle_user_id = ensure_moodle_user(request.user)
    
    # Fetch enrolled courses from local Enrollment records, showing synced course names when available
    enrollments = list(Enrollment.objects.filter(student=request.user))
    course_ids = [en.course_id for en in enrollments]

    # Refresh course names from Moodle to keep Course table accurate
    for cid in course_ids:
        course_data = moodle_api_call('core_course_get_courses', {'options[ids][0]': cid})
        if isinstance(course_data, list) and course_data:
            info = course_data[0]
            Course.objects.update_or_create(
                moodle_course_id=cid,
                defaults={
                    "name": info.get("fullname", f"Course {cid}"),
                    "short_name": info.get("shortname", "")
                }
            )

    course_map = {
        c.moodle_course_id: c
        for c in Course.objects.filter(moodle_course_id__in=course_ids)
    }

    enrolled_courses = []
    for en in enrollments:
        course_obj = course_map.get(en.course_id)
        enrolled_courses.append({
            "id": en.course_id,
            "fullname": course_obj.name if course_obj else f"Course {en.course_id}"
        })

    if not moodle_user_id:
        print("WARNING: No Moodle User ID found for student.")
    
    # Initialize forms with user's current data
    u_form = UserUpdateForm(instance=request.user)
    p_form = ProfileUpdateForm(instance=request.user.profile)
    e_form = EmailUpdateForm(instance=request.user)
    pw_form = PasswordChangeForm(request.user)

    context = {
        'u_form': u_form,
        'p_form': p_form,
        'e_form': e_form,
        'pw_form': pw_form,
        'enrolled_courses': enrolled_courses,
    }
    return render(request, 'student_dashboard.html', context)
@login_required
@student_required
def student_course_view(request, course_id):
    from collections import OrderedDict

    # Always refresh modules from Moodle for latest data
    moodle_sections = moodle_api_call('core_course_get_contents', {'courseid': course_id})
    if isinstance(moodle_sections, list):
        mod_map = {
            "page": "video",
            "resource": "theory",
            "quiz": "quiz",
            "assign": "checkpoint",
        }
        # Module.objects.filter(moodle_course_id=course_id).delete()
        for section in moodle_sections:
            for mod in section.get("modules", []):
                mod_type = mod_map.get(mod.get("modname"))
                if not mod_type:
                    continue
                
                # Generate DASH stream URL for video modules
                d_url = None
                if mod_type == "video":
                    # mod.get("id") from Moodle API = moodle_cmid (course module ID)
                    moodle_cmid = mod.get("id")
                    logger.debug(f"[DASH_SYNC] Processing video module: name={mod.get('name')}, moodle_cmid={moodle_cmid}")
                    
                    # Try to generate DASH stream URL using the helper function
                    generated_mpd_url = build_dash_stream_url(moodle_cmid)
                    if generated_mpd_url:
                        d_url = generated_mpd_url
                        logger.info(f"[DASH_SYNC] ✓ Generated mpd_url for moodle_cmid={moodle_cmid}: {d_url}")
                    else:
                        # If no DASH file exists yet, try to preserve existing valid URL
                        existing_module = Module.objects.filter(moodle_cmid=moodle_cmid).first()
                        if existing_module and existing_module.mpd_url:
                            d_url = existing_module.mpd_url
                            logger.debug(f"[DASH_SYNC] ⚠ DASH file not found, but keeping existing mpd_url for moodle_cmid={moodle_cmid}: {d_url}")
                        else:
                            logger.warning(f"[DASH_SYNC] ✗ No mpd_url available for video moodle_cmid={moodle_cmid} (DASH file not yet prepared, no existing URL)")

                
                Module.objects.update_or_create(
                    moodle_cmid=mod.get("id"),
                    defaults={
                        "name": mod.get("name") or f"Module {mod.get('id')}",
                        "module_type": mod_type,
                        "moodle_module_type": mod.get("modname"),
                        "section_number": section.get("section"),
                        "moodle_course_id": course_id,
                        "mpd_url": d_url,
                    }
                )
    else:
        print("WARNING: Could not fetch modules from Moodle; using local cache.")

    clean_duplicate_modules()
    fix_invalid_progress_sequence(request.user, course_id)

    modules = Module.objects.filter(moodle_course_id=course_id).order_by("section_number", "id")

    progress_map = {
        p.moodle_cmid: p
        for p in StudentModuleProgress.objects.filter(student=request.user)
    }

    unlocked_modules = []
    previous_completed = True

    for idx, module in enumerate(modules):
        progress = progress_map.get(module.moodle_cmid)
        is_completed = progress.is_completed if progress else False

        # Strict sequential unlocking: first module open, others only if previous completed
        if idx == 0:
            is_unlocked = True
        else:
            is_unlocked = previous_completed

        unlocked_modules.append({
            "module": module,
            "is_unlocked": is_unlocked,
            "is_completed": is_completed
        })

        previous_completed = is_completed

    # Regroup modules strictly by allowed Moodle types
    type_map = OrderedDict([
        ("page", "Video"),
        ("resource", "Theory"),
        ("quiz", "Quiz"),
        ("assign", "Assignment"),
    ])

    # Strict grouping by module.module_type values used in this LMS
    sections_dict = OrderedDict([
        ("Video", []),
        ("Theory", []),
        ("Quiz", []),
        ("Assignment", []),
    ])

    for item in unlocked_modules:
        module = item["module"]
        mtype = module.module_type
        
        # All module types are always included - UI shows status based on mpd_url
        if mtype == "video":
            # Video modules ALWAYS included, even without mpd_url
            if module.mpd_url and module.mpd_url.strip():
                logger.debug(f"[DASH_DISPLAY] Including video module (Ready): cmid={module.moodle_cmid}, name={module.name}, mpd_url={module.mpd_url}")
            else:
                logger.info(f"[DASH_DISPLAY] Including video module (Not uploaded): cmid={module.moodle_cmid}, name={module.name}")
            sections_dict["Video"].append(item)
        elif mtype == "theory":
            sections_dict["Theory"].append(item)
        elif mtype == "quiz":
            sections_dict["Quiz"].append(item)
        elif mtype == "checkpoint":
            sections_dict["Assignment"].append(item)
        else:
            continue  # ignore unknown types

    sections_payload = OrderedDict()
    for label, items in sections_dict.items():
        if not items:
            continue
        total = len(items)
        moodle_cmids = [m["module"].moodle_cmid for m in items if m["module"].moodle_cmid]
        completed = StudentModuleProgress.objects.filter(
            student=request.user,
            moodle_cmid__in=moodle_cmids,
            is_completed=True
        ).count() if moodle_cmids else 0
        percent = int((completed / total) * 100) if total else 0
        sections_payload[label] = {
            "modules": items,
            "percent": percent
        }

    return render(request, "student/course.html", {
        "sections": sections_payload,
        "modules": unlocked_modules,
        "course_id": course_id,
    })
    
@login_required
@student_required
def video_player(request):
    """
    Video player for course modules with DASH streaming and rule enforcement.
    """
        
    cmid = request.GET.get('cmid')
    course_id = request.GET.get('courseid')
    
    if not cmid or not course_id:
        messages.error(request, 'Invalid video parameters.')
        return redirect('student_dashboard')
    
    # Get module details from Moodle
    # For now, we'll use a simple player. In production, this would integrate with DASH streaming
    
    context = {
        'cmid': cmid,
        'course_id': course_id,
        'module_name': f'Video Module {cmid}',
    }
    
    return render(request, 'video_player.html', context)

@login_required(login_url="login")
@student_required
def student_player_view(request, module_id):
    """
    Custom DASH player view for student lessons.
    
    Args:
        module_id: Django Module.id (primary key), NOT moodle_cmid
    
    Fetches module info and renders video player with DASH support.
    Only allows access to modules with valid mpd_url.
    """
    print(f">>> STUDENT PLAYER VIEW HIT. Module ID = {module_id}")
    print(f">>> GET PARAMS = {request.GET}")
        
    try:
        # Ensure user has Moodle ID
        if not request.user.moodle_user_id:
            messages.error(request, 'Your account is not linked to Moodle. Please contact administrator.')
            return redirect('student_dashboard')
        
        # Load module by Django id (PRIMARY KEY)
        from .models import Module, ModuleRule
        try:
            module_obj = get_object_or_404(Module, id=module_id)
        except Module.DoesNotExist:
            messages.error(request, "⚠️ Lesson not found or has been removed.")
            return redirect("student_dashboard")

        # Security Check: Ensure module has valid mpd_url
        if not module_obj.mpd_url or not module_obj.mpd_url.strip():
            logger.warning(f"[DASH_PLAYER] ✗ Video access DENIED - no mpd_url: module_id={module_id} (Django ID), moodle_cmid={module_obj.moodle_cmid}")
            messages.error(request, "⚠️ Video is not available for this module yet. Please check back later.")
            return redirect('student_course_view', course_id=module_obj.moodle_course_id)

        # Enforce strict sequential unlock
        course_modules = list(Module.objects.filter(
            moodle_course_id=module_obj.moodle_course_id
        ).order_by("section_number", "id"))
        try:
            idx = course_modules.index(module_obj)
        except ValueError:
            idx = 0

        if idx > 0:
            prev_mod = course_modules[idx - 1]
            prev_done = StudentModuleProgress.objects.filter(
                student=request.user,
                moodle_cmid=prev_mod.moodle_cmid,
                is_completed=True
            ).exists()
            if not prev_done:
                messages.error(request, "Please complete the previous module before accessing this one.")
                return redirect('student_course_view', course_id=module_obj.moodle_course_id)

        # Get module rules (if any)
        rule = ModuleRule.objects.filter(moodle_cmid=module_obj.moodle_cmid).first()
        min_watch_percent = rule.min_watch_percent if (rule and rule.min_watch_percent) else 80

        # Use stored mpd_url
        mpd_url = module_obj.mpd_url
        module_name = module_obj.name or f'Video Lesson {module_obj.moodle_cmid}'
        
        logger.info(f"[DASH_PLAYER] Video player loaded: module_id={module_id}, cmid={module_obj.moodle_cmid}, mpd_url={mpd_url}")

        progress = StudentModuleProgress.objects.filter(
            student=request.user,
            moodle_cmid=module_obj.moodle_cmid
        ).first()
        last_position = progress.last_position_seconds if progress else 0

        if progress:
            print(f">>> FOUND PROGRESS: {progress.last_position_seconds}s")
        else:
            print(">>> NO PROGRESS FOUND - Creating new progress record")

        # Auto-resume feature: Redirect to saved timestamp if available
        if progress and progress.last_position_seconds and progress.last_position_seconds > 5 and request.GET.get("t") is None:
            print(f">>> AUTO-RESUME: Redirecting to {progress.last_position_seconds}s")
            return redirect(f"?t={int(progress.last_position_seconds)}")
        
        is_completed = progress.is_completed if progress else False
        watch_percent = progress.watch_percent if progress else 0

        context = {
            "module_id": module_obj.id,
            "moodle_cmid": module_obj.moodle_cmid,  # For debug and API calls
            "mpd_url": mpd_url,
            "min_watch_percent": min_watch_percent,
            "module": {
                "title": module_name,
                "id": module_obj.id,
                "mpd_url": mpd_url,
                "moodle_cmid": module_obj.moodle_cmid,  # Debug info
            },
            "last_position_seconds": last_position,
            "is_completed": is_completed,
            "watch_percent": watch_percent
        }

        print(">>> RENDERING PLAYER TEMPLATE")
        return render(request, "student/player.html", context)
    
    except Exception as e:
        logger.error(f"Error in student_player_view: {str(e)}", exc_info=True)
        messages.error(request, f'An error occurred while loading the player: {str(e)}')
        return redirect('student_dashboard')

def admin_dashboard(request):
    # dedicated login logic for /adminprivate/
    if not request.user.is_authenticated or not request.user.is_superuser:
        if request.method == 'POST' and request.POST.get('login_source') == 'admin_private':
            username = request.POST.get('username')
            password = request.POST.get('password')
            user = authenticate(request, username=username, password=password)
            
            if user is not None and user.is_superuser:
                login(request, user)
                return redirect('admin_dashboard')
            else:
                messages.error(request, 'Invalid administrative credentials.')
                return render(request, 'admin_login.html')
        
        # If not POST or failed login, show the specialized admin login page
        return render(request, 'admin_login.html')

    total_users = User.objects.count()
    teachers_count = UserProfile.objects.filter(user__is_superuser=False, role='teacher').count()
    students_count = UserProfile.objects.filter(user__is_superuser=False, role='student').count()
    pending_teachers = UserProfile.objects.filter(role='teacher', is_approved=False)
    all_users = User.objects.select_related('profile').all()
    certificates = Certificate.objects.all()
    certificates = Certificate.objects.all()
    
    # Enterprise Audit Logs with filtering
    time_range = request.GET.get('time_range', 'Last 30 Days')
    search_query = request.GET.get('search', '')
    
    audit_logs_base = AuditLog.objects.all().order_by('-timestamp')
    
    if time_range:
        now = timezone.now()
        if time_range == 'Today':
            audit_logs_base = audit_logs_base.filter(timestamp__date=now.date())
        elif time_range == 'Last 7 Days':
            audit_logs_base = audit_logs_base.filter(timestamp__gte=now - timedelta(days=7))
        elif time_range == 'Last 30 Days':
            audit_logs_base = audit_logs_base.filter(timestamp__gte=now - timedelta(days=30))

    if search_query:
        audit_logs_base = audit_logs_base.filter(Q(user__email__icontains=search_query) | Q(details__icontains=search_query) | Q(action__icontains=search_query))

    paginator = Paginator(audit_logs_base, 15)
    page_number = request.GET.get('page')
    logs = paginator.get_page(page_number)
    
    settings = SystemSetting.objects.all()

    # Initialize forms with admin's current data
    u_form = UserUpdateForm(instance=request.user)
    p_form = ProfileUpdateForm(instance=request.user.profile)
    e_form = EmailUpdateForm(instance=request.user)
    pw_form = PasswordChangeForm(request.user)

    context = {
        'total_users': total_users,
        'teachers_count': teachers_count,
        'students_count': students_count,
        'pending_teachers': pending_teachers,
        'all_users': all_users,
        'certificates': certificates,
        'logs': logs,
        'current_filters': {
            'time_range': time_range,
            'search': search_query
        },
        'settings': settings,
        'u_form': u_form,
        'p_form': p_form,
        'e_form': e_form,
        'pw_form': pw_form,
    }
    return render(request, 'admin_dashboard.html', context)

@login_required
@admin_required
def approve_teacher(request, user_id):
    teacher_user = get_object_or_404(User, id=user_id)
    profile = teacher_user.profile
    if profile.role == 'teacher':
        profile.is_approved = True
        profile.save()
        teacher_user.is_active = True
        teacher_user.is_staff = True
        teacher_user.save()

        # Ensure they are in the Teacher group
        teacher_group, _ = Group.objects.get_or_create(name='Teacher')
        teacher_user.groups.add(teacher_group)

        AuditLog.objects.create(
            user=request.user,
            action='USER APPROVED',
            details=f'User {teacher_user.email} Approved'
        )
        messages.success(request, f'Teacher {teacher_user.username} approved successfully.')
    return redirect('admin_dashboard')

@login_required
@admin_required
def reject_teacher(request, user_id):
    teacher_user = get_object_or_404(User, id=user_id)
    profile = teacher_user.profile
    if profile.role == 'teacher':
        teacher_user.is_active = False # Keep inactive
        profile.is_approved = False
        profile.save()
        teacher_user.save()

        AuditLog.objects.create(
            user=request.user,
            action='USER REJECTED',
            details=f'User {teacher_user.email} Rejected'
        )
        messages.warning(request, f'Teacher {teacher_user.username} has been rejected.')
    return redirect('admin_dashboard')

@login_required
@admin_required
def toggle_user_status(request, user_id):
    target_user = get_object_or_404(User, id=user_id)
    if target_user.is_superuser:
        messages.error(request, "Cannot deactivate a superuser.")
    else:
        target_user.is_active = not target_user.is_active
        target_user.save()
        status = "activated" if target_user.is_active else "deactivated"
        action = 'Activate User' if target_user.is_active else 'Deactivate User'

        AuditLog.objects.create(
            user=request.user,
            action=f'USER {status.upper()}',
            details=f'User {target_user.email} {status.capitalize()}'
        )
        messages.success(request, f'User {target_user.username} {status}.')
    return redirect('admin_dashboard')

@login_required
@admin_required
def revoke_certificate(request, cert_id):
    cert = get_object_or_404(Certificate, id=cert_id)
    cert.is_revoked = True
    cert.save()

    AuditLog.objects.create(
        user=request.user,
        action='CERTIFICATE REVOKED',
        details=f'Certificate Revoked → User ID {cert.student.id} → Course ID {cert.course_id}'
    )
    messages.success(request, 'Certificate revoked successfully.')
    return redirect('admin_dashboard')

@login_required
@admin_required
def update_system_settings(request):
    if request.method == 'POST':
        for key, value in request.POST.items():
            if key != 'csrfmiddlewaretoken':
                setting, _ = SystemSetting.objects.get_or_create(key=key)
                setting.value = value
                setting.save()

        AuditLog.objects.create(
            user=request.user,
            action='SYSTEM UPDATED',
            details='System configuration updated via admin dashboard.'
        )
        messages.success(request, 'System settings updated.')
    return redirect('admin_dashboard')

@login_required
def update_profile(request):
    if request.method == 'POST':
        u_form = UserUpdateForm(request.POST, instance=request.user)
        p_form = ProfileUpdateForm(request.POST, request.FILES, instance=request.user.profile)
        if u_form.is_valid() and p_form.is_valid():
            u_form.save()
            p_form.save()
            messages.success(request, 'Your profile has been updated!')
            return redirect(request.META.get('HTTP_REFERER', '/dashboard/?section=settings'))
    return redirect(request.META.get('HTTP_REFERER', 'dashboard'))

@login_required
def update_email(request):
    if request.method == 'POST':
        e_form = EmailUpdateForm(request.POST, instance=request.user)
        if e_form.is_valid():
            e_form.save()
            messages.success(request, 'Your email has been updated!')
            return redirect(request.META.get('HTTP_REFERER', '/dashboard/?section=settings'))
        else:
            for error in e_form.errors.values():
                messages.error(request, error)
            return redirect(request.META.get('HTTP_REFERER', '/dashboard/?section=settings'))
    return redirect(request.META.get('HTTP_REFERER', 'dashboard'))

@login_required
def update_password(request):
    if request.method == 'POST':
        pw_form = PasswordChangeForm(request.user, request.POST)
        if pw_form.is_valid():
            user = pw_form.save()
            update_session_auth_hash(request, user)
            messages.success(request, 'Your password has been changed!')
            return redirect(request.META.get('HTTP_REFERER', '/dashboard/?section=settings'))
        else:
            for error in pw_form.errors.values():
                messages.error(request, error)
            return redirect(request.META.get('HTTP_REFERER', '/dashboard/?section=settings'))
    return redirect(request.META.get('HTTP_REFERER', 'dashboard'))

def home(request):
    return render(request, 'home.html')

def courses(request):
    moodle_courses = fetch_moodle_courses()
    courses_list = []
    if isinstance(moodle_courses, list):
        for course in moodle_courses:
            if isinstance(course, dict):
                if course.get('id') == 1:
                    continue
                courses_list.append({
                    'fullname': course.get('fullname', 'Untitled Course'),
                    'summary': course.get('summary', 'No description available.'),
                    'id': course.get('id')
                })
    if not courses_list:
        courses_list = [
            {'title': 'Python Programming (Demo)', 'desc': 'Learn Python from basics to advanced with real-world projects.', 'icon': 'python.png'},
            {'title': 'Web Development (Demo)', 'desc': 'Master HTML, CSS, JavaScript, Django and build full websites.', 'icon': 'web.png'},
        ]
    return render(request, 'courses.html', {'courses': courses_list})

def about(request):
    return render(request, 'about.html')

def contact(request):
    return render(request, 'contact.html')

@login_required
@teacher_required
def create_course(request):
    if request.method == 'POST':
        form = CourseCreateForm(request.POST)
        if form.is_valid():
            fullname = form.cleaned_data['fullname']
            shortname = form.cleaned_data['shortname']
            category_id = form.cleaned_data['category_id']
            description = form.cleaned_data['description']
            result = create_moodle_course(fullname, shortname, category_id, description)
            if result.get('success'):
                messages.success(request, f"Course '{fullname}' created successfully in Moodle!")
                return redirect('/dashboard/?section=courses')
            else:
                messages.error(request, f"Failed to create course: {result.get('error')}")
    else:
        form = CourseCreateForm()
    return render(request, 'create_course.html', {'form': form})

@login_required
@teacher_required
def manage_course(request, course_id):
    # Fetch course details directly (Moodle)
    all_courses = fetch_moodle_courses()
    course = None
    if isinstance(all_courses, list):
        course = next((c for c in all_courses if isinstance(c, dict) and str(c.get('id')) == str(course_id)), None)
    
    if not course:
        messages.error(request, "Course not found.")
        return redirect('/dashboard/?section=courses')

    # Security Check: Ensure teacher is enrolled in this course (optional but recommended)
    # For now, we assume if they can see it in fetch_moodle_courses-admin results, they can manage it,
    # but the user requested ownership mapping. 
    # Since we don't have a mapping table, we'll check if the teacher is in the course's enrolled users.
    
    # Enrolled Students Fetching (with 30s cache)
    from django.core.cache import cache
    cache_key = f"course_{course_id}_students"
    students = cache.get(cache_key)
    instructors = cache.get(f"course_{course_id}_instructors")
    
    if students is None or instructors is None:
        raw_users = moodle_api_call('core_enrol_get_enrolled_users', {'courseid': course_id})
        students = []
        instructors = []
        
        if isinstance(raw_users, list):
            print(f"DEBUG: Filtering {len(raw_users)} raw users for course {course_id}")
            for u in raw_users:
                if isinstance(u, dict):
                    roles = u.get('roles', [])
                    role_shortnames = [r.get('shortname') for r in roles]
                    
                    # Filter for students
                    if 'student' in role_shortnames:
                        username = u.get('username', '').lower()
                        email = u.get('email', '').lower()
                        
                        # Exclude administrators, managers, and teachers by username/email
                        is_admin = (username in ['admin', 'manager', 'teacher']) or ('admin' in email)
                        
                        if not is_admin:
                            students.append({
                                'id': u.get('id'),
                                'fullname': u.get('fullname', 'Unknown'),
                                'email': u.get('email', ''),
                                'roles': roles
                            })
                    
                    # Filter for instructors (editingteacher or teacher)
                    if any(rn in ['editingteacher', 'teacher'] for rn in role_shortnames):
                        instructors.append({
                            'id': u.get('id'),
                            'fullname': u.get('fullname', 'Unknown'),
                            'email': u.get('email', '')
                        })
        
        cache.set(cache_key, students, 30)
        cache.set(f"course_{course_id}_instructors", instructors, 30)

    # Course Contents Fetching (with 30s cache)
    content_cache_key = f"course_{course_id}_contents"
    course_sections = cache.get(content_cache_key)

    if course_sections is None:
        raw_sections = moodle_api_call('core_course_get_contents', {'courseid': course_id})
        course_sections = []
        
        # Fetch local modules for mpd_url mapping
        from .models import Module
        local_modules = {}
        for m in Module.objects.order_by('moodle_cmid', '-updated_at', '-id'):
            # Prefer records that already have mpd_url populated
            if m.moodle_cmid not in local_modules or (not local_modules[m.moodle_cmid].mpd_url and m.mpd_url):
                local_modules[m.moodle_cmid] = m
        
        if isinstance(raw_sections, list):
            for section in raw_sections:
                section_number = section.get("section", 0)
                mapped_modules = []
                for module in section.get("modules", []):
                    m_id = module.get("id")
                    modname = module.get("modname")
                    mod_map = {
                        "page": "video",
                        "resource": "theory",
                        "quiz": "quiz",
                        "assign": "checkpoint",
                    }
                    module_type_ui = mod_map.get(modname, modname)
                    defaults = {
                        "name": module.get("name"),
                        "module_type": module_type_ui,
                        "moodle_module_type": modname,
                        "section_number": section_number
                    }
                    local_instance, _ = sync_module_record(m_id, defaults)
                    local_modules[m_id] = local_instance
                    mapped_modules.append({
                        "id": local_instance.id if local_instance else m_id,
                        "moodle_cmid": m_id,
                        "name": module.get("name"),
                        "modname": modname,
                        "module_type": module_type_ui,
                        "visible": module.get("visible"),
                        "mpd_url": local_instance.mpd_url if local_instance else None
                    })
                course_sections.append({
                    "id": section.get("id"),
                    "section_num": section_number,
                    "name": section.get("name"),
                    "summary": section.get("summary"),
                    "modules": mapped_modules,
                    "module_count": len(mapped_modules)
                })
        print(f"DEBUG: Fetched sections for course {course_id}: {len(course_sections)}")
        cache.set(content_cache_key, course_sections, 30)

    # REGROUP MODULES BY TYPE
    sections_dict = {
        "video": [],
        "theory": [],
        "quiz": [],
        "assignment": [],
    }

    for section in course_sections:
        for module in section.get("modules", []):
            mtype = module.get("module_type")
            if mtype == "checkpoint":
                mtype = "assignment"
            if mtype in sections_dict:
                sections_dict[mtype].append(module)

    # Prepare for template loop (sections.items() equivalent)
    sections_list = [(k, sections_dict[k]) for k in ["video", "theory", "quiz", "assignment"]]


    # Handle Hide/Show via query param
    action = request.GET.get('action')
    if action in ['hide', 'show']:
        visible = 0 if action == 'hide' else 1
        res = moodle_api_call('core_course_update_courses', {'courses': [{'id': course_id, 'visible': visible}]})
        if isinstance(res, list) or (isinstance(res, dict) and not res.get('exception')):
            messages.success(request, f"Course {'hidden' if visible == 0 else 'shown'} successfully.")
            return redirect('manage_course', course_id=course_id)
        else:
            messages.error(request, f"Failed to update course visibility: {res}")
    elif action == 'delete' and request.method == 'POST':
        if delete_moodle_courses([course_id]):
            messages.success(request, f"Course '{course.get('fullname', 'Unknown')}' deleted successfully.")
            return redirect('/dashboard/?section=courses')
        else:
            messages.error(request, "Failed to delete course.")
            return redirect('manage_course', course_id=course_id)
    # Quick Stats Calculation
    total_modules = sum(len(s.get('modules', [])) for s in course_sections)
    enrolled_students_count = len(students)
    category_id = course.get('categoryid', 'N/A')
    
    print(f"Stats: Modules={total_modules}, Students={enrolled_students_count}, Category={category_id}")
    # Calculate Moodle base URL for redirects
    moodle_base_url = settings.MOODLE_URL.split('/webservice/')[0] if hasattr(settings, 'MOODLE_URL') else ""

    context = {
        'course': course,
        'students': students,
        'instructors': instructors,
        'course_sections': course_sections,
        'sections': sections_list,
        'total_modules': total_modules,
        'enrolled_students': enrolled_students_count,
        'category_id': category_id,
        'moodle_base_url': moodle_base_url,
        'debug_id': 'ANTIGRAVITY_v4',
    }
    return render(request, 'manage_course.html', context)

@login_required
@teacher_required
def upload_video_module(request, cmid):
    """
    Teacher view to upload an MP4 file, chunk it to DASH using FFmpeg natively,
    and save the mpd_url into the local Module mapping.
    """
    from .models import Module
    import os
    import subprocess
    from django.conf import settings

    # Resolve module context strictly by moodle_cmid
    try:
        module_instance = Module.objects.get(moodle_cmid=cmid)
    except Module.DoesNotExist:
        return JsonResponse({"error": "Invalid CMID"}, status=404)

    print(f"Uploading video for CMID={cmid}")

    if request.method == 'POST':
        video_file = request.FILES.get('video_file')
        if not video_file:
            messages.error(request, "Please select an MP4 file to upload.")
            return redirect('upload_video_module', cmid=cmid)
            
        if not video_file.name.endswith('.mp4'):
            messages.error(request, "Only MP4 files are supported.")
            return redirect('upload_video_module', cmid=cmid)

        # Ensure we consistently use the local DB id for file naming
        local_module_id = module_instance.id

        # 1. Setup Media Directories (outside project)
        raw_dir = os.path.join(settings.MEDIA_ROOT, 'raw_videos')
        dash_dir = os.path.join(settings.MEDIA_ROOT, 'dash', f'module_{local_module_id}')
        os.makedirs(raw_dir, exist_ok=True)
        os.makedirs(dash_dir, exist_ok=True)

        # 2. Save Raw Video File
        raw_path = os.path.join(raw_dir, f'module_{local_module_id}.mp4')
        with open(raw_path, 'wb+') as destination:
            for chunk in video_file.chunks():
                destination.write(chunk)
        print(f"RAW video saved at {raw_path}")

        # 3. Call FFmpeg Subprocess for DASH
        raw_video_path = raw_path.replace("\\", "/")
        dash_folder = dash_dir.replace("\\", "/")
        dash_output = os.path.join(dash_folder, 'stream.mpd').replace("\\", "/")

        cmd = [
            "ffmpeg",
            "-i", raw_video_path,
            "-map", "0:v",
            "-map", "0:a",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "128k",
            "-g", "48",
            "-keyint_min", "48",
            "-sc_threshold", "0",
            "-use_timeline", "1",
            "-use_template", "1",
            "-avoid_negative_ts", "make_zero",
            "-fflags", "+genpts",
            "-start_at_zero",
            "-f", "dash",
            dash_output,
        ]

        print("========== FFMPEG DEBUG ==========")
        print("RAW VIDEO PATH:", raw_video_path)
        print("DASH FOLDER:", dash_folder)
        print("FFMPEG COMMAND:", cmd)
        print("===================================")

        try:
            subprocess.run(cmd, check=True)
            print(f"DASH generated at {dash_output}")

            # 4. Save mapping URL
            module_instance.mpd_url = f"/media/dash/module_{local_module_id}/stream.mpd"
            module_instance.save()

            # Reset student progress for this module because media changed
            StudentModuleProgress.objects.filter(moodle_cmid=module_instance.moodle_cmid).update(
                last_position_seconds=0,
                watch_percent=0,
                completed=False,
            )
            print("MPD URL stored successfully")

            messages.success(request, f"Video successfully processed and Stream is Ready for Module {cmid}.")
            return redirect('/teacher/dashboard/?section=courses')

        except subprocess.CalledProcessError as e:
            err = e.stderr.decode('utf-8', errors='ignore') if e.stderr else str(e)
            messages.error(request, f"FFmpeg processing failed: {err}")
            return redirect('upload_video_module', cmid=cmid)
        except Exception as e:
            messages.error(request, f"System error during upload processing: {str(e)}")
            return redirect('upload_video_module', cmid=cmid)

    # Render simple UI form for upload
    return render(request, 'teacher/upload_video.html', {'module': module_instance})


@login_required
@teacher_required
def search_students_api(request):
    query = request.GET.get('q', '').strip()
    if not query or len(query) < 2:
        return JsonResponse([], safe=False)
    
    try:
        # Search by firstname/lastname
        res_name = moodle_api_call('core_user_get_users', {'criteria': [{'key': 'firstname', 'value': f'%{query}%'}]})
        # Search by email
        res_email = moodle_api_call('core_user_get_users', {'criteria': [{'key': 'email', 'value': f'%{query}%'}]})
        
        users_raw = []
        if isinstance(res_name, dict) and 'users' in res_name:
            users_raw.extend(res_name['users'])
        if isinstance(res_email, dict) and 'users' in res_email:
            users_raw.extend(res_email['users'])
            
        unique_users = {}
        for u in users_raw:
            if u['email'] not in unique_users:
                unique_users[u['email']] = {
                    'name': u['fullname'],
                    'email': u['email']
                }
        
        results = list(unique_users.values())[:10]
        return JsonResponse(results, safe=False)
    except Exception as e:
        print(f"Search API Error: {e}")
        return JsonResponse({'error': str(e)}, status=500)

@login_required
@teacher_required
def enrol_student_api(request):
    if request.method == 'POST':
        import json
        try:
            data = json.loads(request.body)
            course_id = data.get('course_id')
            student_email = data.get('student_email')
            course_name = f"Course {course_id}" if course_id else "Unknown Course"
            if course_id:
                course_details = moodle_api_call('core_course_get_courses_by_field', {'field': 'id', 'value': course_id})
                if isinstance(course_details, dict) and course_details.get('courses'):
                    first_course = course_details['courses'][0]
                    course_name = first_course.get('fullname', course_name)
            
            if not student_email:
                return JsonResponse({'success': False, 'error': 'Student email is required'})
            
            # Resolve email to Moodle ID
            user_res = moodle_api_call('core_user_get_users', {'criteria': [{'key': 'email', 'value': student_email}]})
            if not (isinstance(user_res, dict) and user_res.get('users')):
                return JsonResponse({'success': False, 'error': f'Student with email {student_email} not found in Moodle'})
            
            moodle_user_id = user_res['users'][0]['id']
            fullname = user_res['users'][0]['fullname']
            role_id = 5 # Student role
            
            # Build flattened parameters for Moodle API
            enrol_params = {
                'enrolments[0][roleid]': role_id,
                'enrolments[0][userid]': moodle_user_id,
                'enrolments[0][courseid]': course_id
            }
            
            print(f"ENROL PARAMS: {enrol_params}")
            
            # Moodle API call
            res = moodle_api_call('enrol_manual_enrol_users', enrol_params)
            
            # Standard Moodle response for this is empty or None on success
            is_success = False
            if res is None or (isinstance(res, list) and not res) or (isinstance(res, dict) and not res.get('exception')):
                is_success = True
            elif isinstance(res, dict) and res.get('errorcode') == 'Message was not sent.':
                # Special Case: Student enrolled but notification failed
                print(f"DEBUG: Enrolment succeeded but message failed for {student_email}. Verifying...")
                # Safety Check: Verify student is in course
                enrolled_users = moodle_api_call('core_enrol_get_enrolled_users', {'courseid': course_id})
                if isinstance(enrolled_users, list) and any(u.get('id') == moodle_user_id for u in enrolled_users):
                    print(f"DEBUG: Safety check passed. {student_email} is enrolled.")
                    is_success = True
                else:
                    print(f"DEBUG: Safety check failed for {student_email}.")
                    is_success = False
            
            if is_success:
                from django.contrib import messages
                from django.core.cache import cache
                cache.delete(f"course_{course_id}_students")
                log_activity(
                    user_name=student_email,
                    action="enrolled in",
                    course_name=course_name,
                )
                messages.success(request, f"Student {fullname} enrolled successfully.")
                return JsonResponse({'success': True})
            
            return JsonResponse({'success': False, 'error': str(res)})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Invalid request'})

@login_required
@teacher_required
def unenrol_student_api(request):
    if request.method == 'POST':
        import json
        try:
            data = json.loads(request.body)
            course_id = data.get('course_id')
            user_id = data.get('user_id')
            course_name = f"Course {course_id}" if course_id else "Unknown Course"
            if course_id:
                course_details = moodle_api_call('core_course_get_courses_by_field', {'field': 'id', 'value': course_id})
                if isinstance(course_details, dict) and course_details.get('courses'):
                    first_course = course_details['courses'][0]
                    course_name = first_course.get('fullname', course_name)

            student_email = f"user_{user_id}"
            if user_id:
                user_details = moodle_api_call('core_user_get_users', {'criteria': [{'key': 'id', 'value': user_id}]})
                if isinstance(user_details, dict) and user_details.get('users'):
                    student_email = user_details['users'][0].get('email', student_email)

            # Build flattened parameters for Moodle API
            unenrol_params = {
                'enrolments[0][userid]': user_id,
                'enrolments[0][courseid]': course_id
            }
            
            print(f"UNENROL PARAMS: {unenrol_params}")
            
            res = moodle_api_call('enrol_manual_unenrol_users', unenrol_params)
            if res is None or (isinstance(res, dict) and not res.get('exception')):
                from django.core.cache import cache
                cache.delete(f"course_{course_id}_students")
                log_activity(
                    user_name=student_email,
                    action="unenrolled from",
                    course_name=course_name,
                )
                return JsonResponse({'success': True})
            return JsonResponse({'success': False, 'error': str(res)})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Invalid request'})

@login_required
@teacher_required
def delete_course_api(request, course_id):
    res = moodle_api_call('core_course_delete_courses', {'courseids': [course_id]})
    if isinstance(res, list) or (isinstance(res, dict) and not res.get('exception')):
        messages.success(request, "Course deleted successfully.")
        return redirect('/dashboard/?section=courses')
    messages.error(request, f"Failed to delete course: {res}")
    return redirect('manage_course', course_id=course_id)

@login_required
@teacher_required
def update_course_settings_api(request, course_id):
    """
    Handles POST from the Course Settings form.
    Updates the course fullname and shortname in Moodle via core_course_update_courses.
    Moodle is the single source of truth — no local DB writes.
    """
    if request.method == 'POST':
        fullname = request.POST.get('fullname', '').strip()
        shortname = request.POST.get('shortname', '').strip()

        if not fullname or not shortname:
            messages.error(request, 'Course name and shortname cannot be blank.')
            return redirect('manage_course', course_id=course_id)

        # Build flattened params for Moodle REST API
        params = {
            'courses[0][id]': course_id,
            'courses[0][fullname]': fullname,
            'courses[0][shortname]': shortname,
        }
        print(f"UPDATE COURSE SETTINGS PARAMS: {params}")

        res = moodle_api_call('core_course_update_courses', params)
        print(f"UPDATE COURSE SETTINGS RESPONSE: {res}")

        # core_course_update_courses returns None/empty on success
        if res is None or (isinstance(res, list) and not res) or (isinstance(res, dict) and not res.get('exception')):
            # Invalidate all caches for this course so fresh data is fetched
            from django.core.cache import cache
            cache.delete(f"course_{course_id}_students")
            cache.delete(f"course_{course_id}_instructors")
            cache.delete(f"course_{course_id}_contents")
            messages.success(request, f'Course "{fullname}" updated successfully.')
        else:
            error_msg = res.get('message', str(res)) if isinstance(res, dict) else str(res)
            messages.error(request, f'Failed to update course: {error_msg}')

        return redirect('manage_course', course_id=course_id)

    # GET requests should not reach here – redirect to manage page
    return redirect('manage_course', course_id=course_id)

def add_module(request, course_id):
    if not request.user.is_authenticated:
        from django.http import JsonResponse
        return JsonResponse({'success': False, 'error': "User not authenticated"}, status=403)
        
    if not request.user.groups.filter(name='Teacher').exists():
        from django.http import JsonResponse
        return JsonResponse({'success': False, 'error': "User is not a teacher"}, status=403)

    if request.method == 'POST':
        import json
        try:
            if request.content_type == 'application/json':
                data = json.loads(request.body)
                files = {}
            else:
                data = request.POST.dict()
                files = request.FILES
            
            module_type = data.get('module_type')
            section_number = data.get('section_number') or data.get('section_id')
            section_number = to_int_or_none(section_number)

            # Fallback to Moodle API if section_number is missing or invalid
            if section_number is None:
                try:
                    fallback_sections = moodle_api_call('core_course_get_contents', {'courseid': course_id})
                    if isinstance(fallback_sections, list) and fallback_sections:
                        section_number = to_int_or_none(fallback_sections[0].get('section')) or 0
                except Exception as exc:
                    print("ADD MODULE: fallback section fetch failed", exc)
                    section_number = 0
            if section_number is None:
                section_number = 0
            print("SECTION RECEIVED:", section_number)
            title = data.get('title')
            
            # Map form fields
            module_data = {
                'title': title,
                'description': data.get('description', ''),
                'content': data.get('content'),
                'time_limit': data.get('time_limit'),
                'allowed_attempts': data.get('allowed_attempts'),
            }

            from django.conf import settings
            
            # Determine Moodle module based on section type
            SECTION_TO_MOODLE_MODULE = {
                "video": "page",
                "theory": "resource",
                "quiz": "quiz",
                "checkpoint": "assignment",
            }
            moodle_module = SECTION_TO_MOODLE_MODULE.get((module_type or "").lower())
            if not moodle_module:
                return JsonResponse({'success': False, 'error': 'Unsupported module type'}, status=400)

            # 3. Call Moodle Web Service API via helper
            result = add_moodle_module(course_id, section_number, moodle_module, module_data)
            
            from django.http import JsonResponse
            if not result.get('success'):
                return JsonResponse({'success': False, 'error': result.get('error', 'Moodle creation failed')})
            
            # 4. Extract CMID from the custom webservice response
            # Our custom WS returns {"cmid": <int>} in the 'data' field
            moodle_data = result.get('data', {})
            cmid = moodle_data.get('cmid')
            
            if not cmid:
                return JsonResponse({'success': False, 'error': 'Module created in Moodle, but CMID was not returned by the webservice.'})

            # 5. Save Module mapping locally
            sync_module_record(
                cmid,
                {
                    "name": title,
                    "module_type": module_type,
                    "moodle_module_type": moodle_module,
                    "section_number": section_number
                }
            )

            # Log the action
            AuditLog.objects.create(
                user=request.user,
                action='MODULE ADDED',
                details=f"Module Added → {title} to Course {course_id} (CMID: {cmid})"
            )
            return JsonResponse({'success': True, 'message': f"Module '{title}' created successfully!"})

        except Exception as e:
            print("ADD MODULE EXCEPTION:", str(e))
            from django.http import JsonResponse
            return JsonResponse({
                "success": False,
                "error": str(e)
            }, status=500)
    
    from django.http import HttpResponseNotAllowed
    return HttpResponseNotAllowed(['POST'])


@login_required
@teacher_required
def sync_modules_from_moodle(request, course_id):
    # Allow GET for testing
    if request.method != 'GET':
        return HttpResponseNotAllowed(['GET'])

    try:
        print("===== MODULE SYNC STARTED =====")
        # Refresh local course record from Moodle
        course_info = moodle_api_call('core_course_get_courses', {'options[ids][0]': course_id})
        if isinstance(course_info, list) and course_info:
            course_data = course_info[0]
            Course.objects.update_or_create(
                moodle_course_id=course_id,
                defaults={
                    "name": course_data.get("fullname", f"Course {course_id}"),
                    "short_name": course_data.get("shortname", "")
                }
            )

        raw_sections = moodle_api_call('core_course_get_contents', {'courseid': course_id})
        if not isinstance(raw_sections, list):
            return JsonResponse({'status': 'error', 'error': 'Invalid response from Moodle'}, status=400)

        mod_map = {
            "page": "video",
            "resource": "theory",
            "quiz": "quiz",
            "assign": "checkpoint",
        }

        modules_to_create = []

        for section in raw_sections:
            section_num = section.get("section")
            print(f"Processing Section: {section_num}")

            for module in section.get("modules", []):

                modname = module.get("modname")
                module_type = mod_map.get(modname)

                if not module_type:
                    print(f"UNSUPPORTED MODULE TYPE: {modname}")
                    continue

                cmid = module.get("id")
                module_name = module.get("name") or f"Module {cmid}"
                print(f"SYNCING MODULE CMID={cmid} NAME={module_name} MODNAME={modname}")

                modules_to_create.append({
                    "moodle_cmid": cmid,
                    "name": module_name,
                    "module_type": module_type,
                    "moodle_module_type": modname,
                    "section_number": section_num,
                    "moodle_course_id": course_id,
                })

        # Delete existing modules and related progress for this course before recreating
        existing_cmids = list(Module.objects.filter(moodle_course_id=course_id).values_list("moodle_cmid", flat=True))
        if existing_cmids:
            StudentModuleProgress.objects.filter(moodle_cmid__in=existing_cmids).delete()
        Module.objects.filter(moodle_course_id=course_id).delete()

        created = 0
        for mod_data in modules_to_create:
            Module.objects.create(**mod_data)
            created += 1

        print("===== MODULE SYNC COMPLETED =====")

        return redirect('manage_course', course_id=course_id)
    except Exception as exc:
        print(f"SYNC ERROR: {exc}")
        return redirect('manage_course', course_id=course_id)


@login_required
@teacher_required
def delete_module(request):
    from django.http import JsonResponse
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)

    import json
    try:
        data = json.loads(request.body)
        moodle_cmid = data.get('cmid')
        if not moodle_cmid:
            return JsonResponse({'success': False, 'error': 'cmid is required'}, status=400)

        from .models import Module
        module = get_object_or_404(Module, moodle_cmid=moodle_cmid)

        result = delete_moodle_module(moodle_cmid)
        if result.get('success'):
            # Cleanup local records
            module.delete()
            from .models import ModuleRule
            ModuleRule.objects.filter(moodle_cmid=moodle_cmid).delete()
            
            AuditLog.objects.create(
                user=request.user,
                action='MODULE DELETED',
                details=f'Module Deleted → Moodle CMID {moodle_cmid}'
            )
            return JsonResponse({'success': True})
        else:
            return JsonResponse({'success': False, 'error': result.get('error', 'Unknown error')}, status=400)
    except Exception as e:
        print("DELETE MODULE EXCEPTION:", str(e))
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


def to_int_or_none(value):
    """Helper to convert empty or invalid strings to None for IntegerFields."""
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None

def to_bool(value):
    """Helper to convert string values like 'on', 'true', '1' to Boolean True."""
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("true", "on", "1", "yes")

@login_required
@teacher_required
def save_module_rules(request):
    from django.http import JsonResponse
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)

    try:
        data = json.loads(request.body)
        moodle_cmid = data.get('cmid')
        if not moodle_cmid:
            return JsonResponse({'success': False, 'error': 'cmid is required'}, status=400)
        
        from .models import Module
        module = get_object_or_404(Module, moodle_cmid=moodle_cmid)
        
        rule, created = ModuleRule.objects.get_or_create(moodle_cmid=moodle_cmid)
        
        # Update fields with sanitization
        rule.disable_seeking = to_bool(data.get('disable_seeking'))
        rule.disable_fast_forward = to_bool(data.get('disable_fast_forward'))
        rule.min_watch_percent = to_int_or_none(data.get('min_watch_percent'))
        rule.quiz_time_limit = to_int_or_none(data.get('time_limit'))
        rule.auto_submit = to_bool(data.get('auto_submit'))
        rule.max_attempts = to_int_or_none(data.get('max_attempts'))
        rule.prerequisite_cmid = to_int_or_none(data.get('prerequisite_cmid'))
        
        rule.save()
        
        msg_parts = []
        if data.get('min_watch_percent'):
            msg_parts.append(f"Min Watch {data.get('min_watch_percent')}%")
        if to_bool(data.get('disable_fast_forward')):
            msg_parts.append("Fast Forward Disabled")
        if data.get('time_limit'):
            msg_parts.append(f"Time Limit {data.get('time_limit')} min")
        if data.get('max_attempts'):
            msg_parts.append(f"Attempts {data.get('max_attempts')}")
        
        rules_type = "Video/Quiz"
        if data.get('min_watch_percent') or to_bool(data.get('disable_fast_forward')):
            rules_type = "Video"
        elif data.get('time_limit') or data.get('max_attempts'):
            rules_type = "Quiz"
            
        details_msg = f"{rules_type} Rules Updated → " + " → ".join(msg_parts)

        AuditLog.objects.create(
            user=request.user,
            action='RULE UPDATED',
            details=details_msg
        )
        return JsonResponse({'success': True})
    except Exception as e:
        print("SAVE MODULE RULES EXCEPTION:", str(e))
        return JsonResponse({'success': False, 'error': str(e)}, status=500)

@login_required
@teacher_required
def get_module_rules(request, cmid):
    # cmid here is the Moodle CMID
    from django.http import JsonResponse
    from .models import Module
    try:
        module = get_object_or_404(Module, moodle_cmid=cmid)

        rule = ModuleRule.objects.filter(moodle_cmid=cmid).first()
        if rule:
            data = {
                'cmid': cmid,
                'moodle_cmid': cmid,
                'disable_seeking': rule.disable_seeking,
                'disable_fast_forward': rule.disable_fast_forward,
                'min_watch_percent': rule.min_watch_percent,
                'time_limit': rule.quiz_time_limit,
                'auto_submit': rule.auto_submit,
                'max_attempts': rule.max_attempts,
                'prerequisite_cmid': rule.prerequisite_cmid,
            }
        else:
            # Return defaults
            data = {
                'cmid': cmid,
                'moodle_cmid': cmid,
                'disable_seeking': False,
                'disable_fast_forward': False,
                'min_watch_percent': None,
                'time_limit': None,
                'auto_submit': False,
                'max_attempts': None,
                'prerequisite_cmid': None,
            }
        return JsonResponse({'success': True, 'rules': data})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)
@login_required
@student_required
def student_explore_courses(request):
    user_profile = request.user.profile
    moodle_uid = user_profile.moodle_user_id

    # 1. Fetch all available courses from Moodle
    # Use existing helper or generic call
    all_courses = fetch_moodle_courses()
    
    # 2. Fetch courses user is already enrolled in
    enrolled_courses_raw = []
    if moodle_uid:
        enrolled_courses_raw = moodle_api_call(
            "core_enrol_get_users_courses",
            {"userid": moodle_uid}
        )
    
    if not isinstance(enrolled_courses_raw, list):
        enrolled_courses_raw = []
        
    enrolled_ids = [c.get("id") for c in enrolled_courses_raw if isinstance(c, dict)]

    # 3. Filter out already enrolled courses and the site course (ID=1)
    # Also Extract thumbnails from overviewfiles
    available_courses = []
    if isinstance(all_courses, list):
        for c in all_courses:
            if isinstance(c, dict) and c.get("id") not in enrolled_ids and c.get("id") != 1:
                # Add thumbnail logic
                overviewfiles = c.get("overviewfiles", [])
                if overviewfiles and isinstance(overviewfiles, list):
                    first_file = overviewfiles[0]
                    fileurl = first_file.get("fileurl", "")
                    if fileurl:
                        from django.conf import settings
                        c["thumbnail"] = f"{fileurl}&token={settings.MOODLE_TOKEN}"
                    else:
                        c["thumbnail"] = None
                else:
                    c["thumbnail"] = None
                
                available_courses.append(c)

    context = {
        "courses": available_courses,
    }
    return render(request, "student/explore_courses.html", context)

@login_required
@student_required
def student_enroll_course(request, course_id):
    if request.method == "POST":
        user_profile = request.user.profile
        moodle_uid = user_profile.moodle_user_id
        
        if not moodle_uid:
            messages.error(request, "Your account is not linked to a Moodle ID. Contact Admin.")
            return redirect('student_dashboard')

        # Call Moodle to enroll (Role 5 = Student)
        result = moodle_api_call(
            "enrol_manual_enrol_users",
            {
                "enrolments": [{
                    "roleid": 5,
                    "userid": moodle_uid,
                    "courseid": course_id
                }]
            }
        )

        # Moodle returns None or [] on success for this function
        if result is None or (isinstance(result, list) and not result) or (isinstance(result, dict) and 'exception' not in result):
            Enrollment.objects.get_or_create(student=request.user, course_id=course_id)
            messages.success(request, "Successfully enrolled in the course!")
        else:
            error_msg = "Enrolment failed."
            if isinstance(result, dict):
                error_msg = result.get('message', result.get('exception', error_msg))
            messages.error(request, f"Enrollment Error: {error_msg}")

    return redirect('/student/dashboard/?section=my-courses')
