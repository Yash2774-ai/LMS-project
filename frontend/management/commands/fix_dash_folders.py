"""
Management command to fix mismatched DASH folder naming.

Problem: DASH files generated using Django module.id instead of module.moodle_cmid
Result: /media/dash/module_<django_id>/ but should be /media/dash/module_<moodle_cmid>/

Usage:
    python manage.py fix_dash_folders --diagnose          # Check for mismatches
    python manage.py fix_dash_folders --rename             # Rename folders
    python manage.py fix_dash_folders --status             # List all modules and DASH status
    python manage.py fix_dash_folders --cleanup            # Remove old orphaned folders
"""

import os
import shutil
import logging
from django.core.management.base import BaseCommand
from django.conf import settings
from frontend.models import Module

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Fix mismatched DASH folder naming issues'

    def add_arguments(self, parser):
        parser.add_argument(
            '--diagnose',
            action='store_true',
            help='Diagnose mismatched DASH folders'
        )
        parser.add_argument(
            '--rename',
            action='store_true',
            help='Rename mismatched folders from module_<id> to module_<moodle_cmid>'
        )
        parser.add_argument(
            '--status',
            action='store_true',
            help='List all modules and their DASH status'
        )
        parser.add_argument(
            '--cleanup',
            action='store_true',
            help='Remove orphaned DASH folders (no matching Module record)'
        )

    def handle(self, *args, **options):
        if not hasattr(settings, 'MEDIA_ROOT') or not settings.MEDIA_ROOT:
            self.stdout.write(
                self.style.ERROR("MEDIA_ROOT not configured!")
            )
            return

        dash_root = os.path.join(settings.MEDIA_ROOT, 'dash')
        if not os.path.exists(dash_root):
            self.stdout.write(
                self.style.WARNING(f"DASH root directory not found: {dash_root}")
            )
            return

        if options['diagnose']:
            self.diagnose(dash_root)
        elif options['rename']:
            self.rename_folders(dash_root)
        elif options['status']:
            self.show_status(dash_root)
        elif options['cleanup']:
            self.cleanup_orphaned(dash_root)
        else:
            self.stdout.write(
                "Use --diagnose, --rename, --status, or --cleanup"
            )

    def diagnose(self, dash_root):
        """Check for mismatched DASH folders."""
        self.stdout.write("=" * 80)
        self.stdout.write("🔍 DIAGNOSING DASH FOLDER MISMATCHES")
        self.stdout.write("=" * 80)

        mismatches = []
        
        # Get all existing DASH folders
        dash_folders = [d for d in os.listdir(dash_root) 
                       if d.startswith('module_') and os.path.isdir(os.path.join(dash_root, d))]
        
        self.stdout.write(f"\nFound {len(dash_folders)} DASH folders:\n")
        
        for folder_name in sorted(dash_folders):
            # Extract folder ID
            try:
                folder_id = int(folder_name.replace('module_', ''))
            except ValueError:
                self.stdout.write(self.style.WARNING(f"  ⚠ INVALID folder name: {folder_name}"))
                continue
            
            # Check if this ID exists in Module table
            module_by_django_id = Module.objects.filter(id=folder_id).first()
            module_by_moodle_cmid = Module.objects.filter(moodle_cmid=folder_id).first()
            
            if module_by_moodle_cmid:
                self.stdout.write(
                    self.style.SUCCESS(f"  ✓ {folder_name}: matches moodle_cmid (correct!)")
                )
            elif module_by_django_id:
                actual_moodle_cmid = module_by_django_id.moodle_cmid
                self.stdout.write(
                    self.style.ERROR(
                        f"  ✗ {folder_name}: Django ID, should be module_{actual_moodle_cmid} "
                        f"(moodle_cmid={actual_moodle_cmid}, name={module_by_django_id.name})"
                    )
                )
                mismatches.append({
                    'current': folder_name,
                    'correct': f'module_{actual_moodle_cmid}',
                    'module': module_by_django_id
                })
            else:
                self.stdout.write(
                    self.style.WARNING(f"  ⚠ {folder_name}: No matching Module record (orphaned)")
                )
        
        self.stdout.write("\n" + "=" * 80)
        if mismatches:
            self.stdout.write(
                self.style.ERROR(f"\n⚠ Found {len(mismatches)} MISMATCHED folders:")
            )
            for m in mismatches:
                self.stdout.write(
                    f"  {m['current']} → {m['correct']} "
                    f"({m['module'].name})"
                )
            self.stdout.write(
                self.style.WARNING(
                    "\nRun: python manage.py fix_dash_folders --rename\n"
                )
            )
        else:
            self.stdout.write(self.style.SUCCESS("✓ No mismatches found!"))

    def rename_folders(self, dash_root):
        """Rename mismatched folders from Django ID to moodle_cmid."""
        self.stdout.write("=" * 80)
        self.stdout.write("🔧 RENAMING DASH FOLDERS")
        self.stdout.write("=" * 80)

        renamed_count = 0
        errors = []

        # Get all existing DASH folders
        dash_folders = [d for d in os.listdir(dash_root) 
                       if d.startswith('module_') and os.path.isdir(os.path.join(dash_root, d))]
        
        for folder_name in sorted(dash_folders):
            try:
                folder_id = int(folder_name.replace('module_', ''))
            except ValueError:
                continue

            module_by_django_id = Module.objects.filter(id=folder_id).first()
            module_by_moodle_cmid = Module.objects.filter(moodle_cmid=folder_id).first()

            # If already correct, skip
            if module_by_moodle_cmid:
                self.stdout.write(
                    self.style.SUCCESS(f"  ✓ {folder_name} is already correct")
                )
                continue

            # If needs rename
            if module_by_django_id:
                new_folder_name = f'module_{module_by_django_id.moodle_cmid}'
                old_path = os.path.join(dash_root, folder_name)
                new_path = os.path.join(dash_root, new_folder_name)

                try:
                    # Check if target already exists
                    if os.path.exists(new_path):
                        self.stdout.write(
                            self.style.WARNING(
                                f"  ⚠ Cannot rename {folder_name}: target {new_folder_name} already exists"
                            )
                        )
                        continue

                    os.rename(old_path, new_path)
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  ✓ {folder_name} → {new_folder_name} ({module_by_django_id.name})"
                        )
                    )
                    renamed_count += 1
                except Exception as e:
                    msg = f"  ✗ Error renaming {folder_name}: {str(e)}"
                    self.stdout.write(self.style.ERROR(msg))
                    errors.append(msg)

        self.stdout.write("\n" + "=" * 80)
        self.stdout.write(
            self.style.SUCCESS(f"✓ Renamed {renamed_count} folders")
        )
        if errors:
            self.stdout.write(self.style.ERROR(f"✗ {len(errors)} errors encountered"))

    def show_status(self, dash_root):
        """Show status of all modules and DASH files."""
        self.stdout.write("=" * 100)
        self.stdout.write("📊 MODULE DASH STATUS")
        self.stdout.write("=" * 100)

        modules = Module.objects.filter(module_type='video').order_by('moodle_course_id', 'section_number', 'id')
        
        video_count = 0
        with_dash = 0
        without_dash = 0

        for module in modules:
            video_count += 1
            if module.mpd_url:
                with_dash += 1
                status = "✓"
                status_style = self.style.SUCCESS
            else:
                without_dash += 1
                status = "✗"
                status_style = self.style.ERROR

            expected_folder = os.path.join(dash_root, f'module_{module.moodle_cmid}')
            folder_exists = os.path.exists(expected_folder)
            folder_mark = "📁" if folder_exists else "  "

            line = (
                f"{status_style(status)} Django_ID={module.id:3d} | "
                f"CMID={module.moodle_cmid:3d} | "
                f"{folder_mark} | "
                f"{module.name[:40]:40s} | "
                f"Course={module.moodle_course_id:3d}"
            )
            self.stdout.write(line)

        self.stdout.write("\n" + "=" * 100)
        self.stdout.write(f"Total video modules: {video_count}")
        self.stdout.write(self.style.SUCCESS(f"  ✓ With DASH: {with_dash}"))
        self.stdout.write(self.style.ERROR(f"  ✗ Without DASH: {without_dash}"))

    def cleanup_orphaned(self, dash_root):
        """Remove DASH folders that don't have matching Module records."""
        self.stdout.write("=" * 80)
        self.stdout.write("🗑 CLEANING UP ORPHANED FOLDERS")
        self.stdout.write("=" * 80)

        removed_count = 0
        dash_folders = [d for d in os.listdir(dash_root) 
                       if d.startswith('module_') and os.path.isdir(os.path.join(dash_root, d))]

        for folder_name in sorted(dash_folders):
            try:
                folder_id = int(folder_name.replace('module_', ''))
            except ValueError:
                continue

            # Check if this referenced by any Module
            exists_as_django_id = Module.objects.filter(id=folder_id).exists()
            exists_as_moodle_cmid = Module.objects.filter(moodle_cmid=folder_id).exists()

            if not exists_as_django_id and not exists_as_moodle_cmid:
                folder_path = os.path.join(dash_root, folder_name)
                try:
                    shutil.rmtree(folder_path)
                    self.stdout.write(
                        self.style.SUCCESS(f"  🗑 Removed orphaned folder: {folder_name}")
                    )
                    removed_count += 1
                except Exception as e:
                    self.stdout.write(
                        self.style.ERROR(f"  ✗ Error removing {folder_name}: {str(e)}")
                    )

        self.stdout.write("\n" + "=" * 80)
        self.stdout.write(self.style.SUCCESS(f"✓ Removed {removed_count} orphaned folders"))
