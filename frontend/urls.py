from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('courses/', views.courses, name='courses'),
    path('about/', views.about, name='about'),
    path('contact/', views.contact, name='contact'),
    path('signup/', views.signup, name='signup'),
    path('login/', views.login_view, name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('dashboard/', views.dashboard_redirect, name='dashboard'), 
    path('teacher/dashboard/', views.teacher_dashboard, name='teacher_dashboard'),
    path('student/dashboard/', views.student_dashboard, name='student_dashboard'),
    path('create-course/', views.create_course, name='create_course'),
    path('manage-course/<int:course_id>/', views.manage_course, name='manage_course'),
    path('teacher/module/<int:cmid>/upload-video/', views.upload_video_module, name='upload_video_module'),
    path('teacher/course/<int:course_id>/add-module/', views.add_module, name='add_module'),
    path('teacher/course/<int:course_id>/sync-modules/', views.sync_modules_from_moodle, name='sync_modules'),
    path('teacher/delete-module/', views.delete_module, name='delete_module'),
    path('teacher/save-module-rules/', views.save_module_rules, name='save_module_rules'),
    path('teacher/get-module-rules/<int:cmid>/', views.get_module_rules, name='get_module_rules'),
    path('update-profile/', views.update_profile, name='update_profile'),
    path('update-email/', views.update_email, name='update_email'),
    path('update-password/', views.update_password, name='update_password'),
    path('student/explore-courses/', views.student_explore_courses, name='student_explore_courses'),
    path('student/enroll/<int:course_id>/', views.student_enroll_course, name='student_enroll_course'),
    path('student/course/<int:course_id>/', views.student_course_view, name='student_course_view'),
    path('student/player/<int:module_id>/', views.student_player_view, name='student_player'),
    path('player/', views.video_player, name='video_player'),
    
    # Course Management API
    path('teacher/enrol-student/', views.enrol_student_api, name='enrol_student_api'),
    path('teacher/search-students/', views.search_students_api, name='search_students_api'),
    path('teacher/unenrol-student/', views.unenrol_student_api, name='unenrol_student_api'),
    path('teacher/delete-course/<int:course_id>/', views.delete_course_api, name='delete_course_api'),
    path('teacher/update-course-settings/<int:course_id>/', views.update_course_settings_api, name='update_course_settings_api'),
    
    # Analytics
    path('teacher/analytics/', views.teacher_analytics, name='teacher_analytics'),
    path('api/save-watch-log/', views.save_watch_log, name='save_watch_log'),
    path('student/api/video-complete/', views.video_complete_api, name='video_complete_api'),
    path('api/save-position/', views.save_video_position, name='save_video_position'),
    path('api/video-progress/', views.video_progress_update, name='video_progress_update'),
    
    # Certificate System
    # Certificate System (now part of teacher dashboard section)
    path('teacher/save-cert-template/', views.save_certificate_template, name='save_certificate_template'),
    path('teacher/save-cert-signer/', views.save_certificate_signer, name='save_certificate_signer'),
    path('teacher/issue-certificate/', views.issue_certificate, name='issue_certificate'),
    path('verify-certificate/<str:code>/', views.verify_certificate, name='verify_certificate'),

    
    # Admin Panel
    path('adminprivate/', views.admin_dashboard, name='admin_dashboard'),
    path('adminprivate/approve/<int:user_id>/', views.approve_teacher, name='approve_teacher'),
    path('adminprivate/reject/<int:user_id>/', views.reject_teacher, name='reject_teacher'),
    path('adminprivate/toggle-user/<int:user_id>/', views.toggle_user_status, name='toggle_user_status'),
    path('adminprivate/revoke-cert/<int:cert_id>/', views.revoke_certificate, name='revoke_certificate'),
    path('adminprivate/update-settings/', views.update_system_settings, name='update_system_settings'),
]
