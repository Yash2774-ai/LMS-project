from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.core.exceptions import ValidationError

class UserProfile(models.Model):
    ROLE_CHOICES = (
        ('student', 'Student'),
        ('teacher', 'Teacher'),
    )
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default='student')
    is_approved = models.BooleanField(default=False) # For teacher approval
    bio = models.TextField(max_length=500, blank=True)
    designation = models.CharField(max_length=100, blank=True)
    moodle_user_id = models.IntegerField(null=True, blank=True)
    profile_picture = models.ImageField(upload_to='profile_pics/', blank=True, null=True)

    def __str__(self):
        return f'{self.user.username} Profile'

@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.get_or_create(user=instance)

@receiver(pre_save, sender=User)
def limit_superusers(sender, instance, **kwargs):
    if instance.is_superuser:
        if User.objects.filter(is_superuser=True).exclude(pk=instance.pk).exists():
            raise ValidationError("Only one superuser is allowed in the system.")

@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    if hasattr(instance, 'profile'):
        instance.profile.save()
    else:
        UserProfile.objects.create(user=instance)

# Add moodle_user_id property to User model for convenience
def get_moodle_user_id(self):
    if hasattr(self, 'profile'):
        return self.profile.moodle_user_id
    return None

User.add_to_class('moodle_user_id', property(get_moodle_user_id))

class AuditLog(models.Model):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=255)
    details = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.user} - {self.action} @ {self.timestamp}'

    @property
    def created_at(self):
        return self.timestamp

    @property
    def performed_by(self):
        return self.user.email if self.user else "System"

    @property
    def action_type(self):
        return self.action

    @property
    def message(self):
        return self.details

    @property
    def severity(self):
        # Base logic for severity
        a = self.action.lower()
        if any(kw in a for kw in ['reject', 'revoke', 'delete', 'deactivate']):
            return 'WARNING'
        if any(kw in a for kw in ['critical', 'error', 'fail']):
            return 'CRITICAL'
        return 'INFO'

    @property
    def entity_type(self):
        a = self.action.lower()
        if any(kw in a for kw in ['user', 'teacher', 'student', 'approval']):
            return 'USER'
        if 'course' in a:
            return 'COURSE'
        if any(kw in a for kw in ['rule', 'config', 'video', 'module']):
            return 'RULE'
        if any(kw in a for kw in ['certificate', 'cert']):
            return 'CERTIFICATE'
        return 'SYSTEM'

class ActivityLog(models.Model):
    user_name = models.CharField(max_length=255)
    action = models.CharField(max_length=255)
    course_name = models.CharField(max_length=255, null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=50, default="Success")

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.user_name} {self.action} {self.course_name or ''} @ {self.timestamp}"

    @property
    def created_at(self):
        return self.timestamp

class SystemSetting(models.Model):
    key = models.CharField(max_length=100, unique=True)
    value = models.TextField()
    description = models.TextField(blank=True)

    def __str__(self):
        return self.key

class Course(models.Model):
    moodle_course_id = models.IntegerField(unique=True)
    name = models.CharField(max_length=255)
    short_name = models.CharField(max_length=255, blank=True, default="")

    def __str__(self):
        return f"{self.name} ({self.moodle_course_id})"

class Certificate(models.Model):
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name='certificates')
    course_id = models.IntegerField()
    course_name = models.CharField(max_length=255)
    issued_at = models.DateTimeField(auto_now_add=True)
    is_revoked = models.BooleanField(default=False)

    def __str__(self):
        return f'{self.student.username} - {self.course_name} ({"Revoked" if self.is_revoked else "Active"})'
class Module(models.Model):

    name = models.CharField(max_length=255)

    # ⭐ VERY IMPORTANT → Unique identity from Moodle
    moodle_cmid = models.IntegerField(unique=True, null=True, blank=True)
    moodle_course_id = models.IntegerField(null=True, blank=True, help_text="Moodle Course ID this module belongs to")

    module_type = models.CharField(
        max_length=50,
        help_text="Section type e.g., video, theory, quiz, checkpoint"
    )

    moodle_module_type = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        help_text="Actual Moodle module name (page, resource, quiz, assign)"
    )

    section_number = models.IntegerField(
        null=True,
        blank=True,
        help_text="Moodle section number (sectionnum)"
    )

    # ⭐ Video streaming path
    mpd_url = models.TextField(null=True, blank=True)

    # ⭐ Sync stability timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} (CMID: {self.moodle_cmid})"

    def __str__(self):
        return f'{self.name} ({self.module_type})'

class ModuleRule(models.Model):
    moodle_cmid = models.IntegerField(unique=True, help_text="Moodle Course Module ID")
    prerequisite_cmid = models.IntegerField(null=True, blank=True, help_text="Must complete this module first")
    
    # Video Rules
    disable_seeking = models.BooleanField(default=False)
    disable_fast_forward = models.BooleanField(default=False)
    min_watch_percent = models.IntegerField(null=True, blank=True)
    
    # Quiz Rules
    quiz_time_limit = models.IntegerField(null=True, blank=True, help_text="Time limit in minutes")
    auto_submit = models.BooleanField(default=False)
    max_attempts = models.IntegerField(null=True, blank=True)
    
    # Audit tracking
    updated_at = models.DateTimeField(auto_now=True)


class WatchLog(models.Model):
    user_id = models.IntegerField(help_text="Moodle User ID")
    course_id = models.IntegerField()
    moodle_cmid = models.IntegerField(help_text="Moodle Course Module ID")
    watched_seconds = models.IntegerField()
    total_duration = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"WatchLog: User {self.user_id} - Module {self.moodle_cmid}"

class Enrollment(models.Model):
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="enrollments")
    course_id = models.IntegerField()
    enrolled_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("student", "course_id")

    def __str__(self):
        return f"{self.student.username} enrolled in course {self.course_id}"

class StudentModuleProgress(models.Model):
    student = models.ForeignKey(User, on_delete=models.CASCADE)
    moodle_cmid = models.IntegerField()
    watch_percent = models.FloatField(default=0)
    is_completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)
    last_position_seconds = models.FloatField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("student", "moodle_cmid")


class CertificateTemplate(models.Model):
    """Defines the visual layout and assets for course certificates."""
    teacher = models.ForeignKey(User, on_delete=models.CASCADE, related_name='cert_templates')
    name = models.CharField(max_length=200)
    course_id = models.IntegerField(help_text="Moodle Course ID this template is for", null=True, blank=True)

    background_image = models.ImageField(upload_to='cert_templates/')
    font_family = models.CharField(max_length=100, default='Poppins')

    # Text overlay positions (pixels from top-left of background)
    student_name_x = models.IntegerField(default=500)
    student_name_y = models.IntegerField(default=300)

    course_name_x = models.IntegerField(default=500)
    course_name_y = models.IntegerField(default=380)

    signature_x = models.IntegerField(default=800)
    signature_y = models.IntegerField(default=500)

    qr_x = models.IntegerField(default=900)
    qr_y = models.IntegerField(default=520)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['teacher', 'course_id'])]

    def __str__(self):
        return f'Template: {self.name} (Teacher: {self.teacher.username})'


class CertificateSigner(models.Model):
    """Stores the signature authority details for a teacher."""
    teacher = models.OneToOneField(User, on_delete=models.CASCADE, related_name='cert_signer')
    signer_name = models.CharField(max_length=200)
    designation = models.CharField(max_length=200)
    signature_image = models.ImageField(upload_to='cert_signatures/')

    def __str__(self):
        return f'{self.signer_name} ({self.designation})'


class IssuedCertificate(models.Model):
    """Tracks each certificate issued to a student for a course."""
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name='issued_certificates')
    course_id = models.IntegerField()
    course_name = models.CharField(max_length=255)
    certificate_template = models.ForeignKey(
        CertificateTemplate, on_delete=models.SET_NULL, null=True, blank=True
    )
    verification_code = models.CharField(max_length=64, unique=True)
    certificate_file = models.CharField(max_length=500, blank=True, help_text="Relative path to generated PNG in media/")
    issued_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['verification_code'])]

    def __str__(self):
        return f'Cert #{self.verification_code[:8]} — {self.student.username} / {self.course_name}'
