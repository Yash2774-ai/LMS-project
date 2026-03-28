#!/usr/bin/env python
"""
Quick DASH folder fixing script (Can run standalone or via Django shell)

Purpose:
  Fix the DASH pipeline to use moodle_cmid instead of Django id for folder naming.

Problem:
  - DASH folders generated with: /media/dash/module_<django_id>/stream.mpd
  - Should be: /media/dash/module_<moodle_cmid>/stream.mpd
  - This causes mpd_url mismatch and videos don't load

Usage in Django shell:
  >>> from tmp.fix_moodle_dash import (
  ...     diagnose_dash_issues,
  ...     fix_module_mpd_urls,
  ...     rename_dash_folders,
  ...     get_dash_status
  ... )
  >>> diagnose_dash_issues()
  >>> fix_module_mpd_urls()
  >>> rename_dash_folders()
  >>> get_dash_status()

Or run standalone:
  python manage.py shell < tmp/fix_moodle_dash.py
"""

import os
import shutil
import logging
from pathlib import Path

logger = logging.getLogger('django')


def diagnose_dash_issues():
    """
    Identify DASH folder mismatches.
    Returns dict with mismatch info.
    """
    from django.conf import settings
    from frontend.models import Module
    
    print("\n" + "="*80)
    print("🔍 DIAGNOSING DASH ISSUES")
    print("="*80)
    
    if not hasattr(settings, 'MEDIA_ROOT'):
        print("❌ MEDIA_ROOT not configured")
        return {}
    
    dash_root = Path(settings.MEDIA_ROOT) / 'dash'
    if not dash_root.exists():
        print(f"⚠️  DASH directory doesn't exist: {dash_root}")
        return {}
    
    mismatches = {}
    existing_folders = list(dash_root.glob('module_*'))
    
    print(f"\nFound {len(existing_folders)} DASH folders:")
    
    for folder in sorted(existing_folders):
        folder_name = folder.name
        try:
            folder_id = int(folder_name.replace('module_', ''))
        except ValueError:
            print(f"  ⚠️  Invalid folder name: {folder_name}")
            continue
        
        # Check if folder_id is Django ID or moodle_cmid
        mod_by_django_id = Module.objects.filter(id=folder_id).first()
        mod_by_moodle_cmid = Module.objects.filter(moodle_cmid=folder_id).first()
        
        if mod_by_moodle_cmid:
            print(f"  ✓ {folder_name}: Correct (moodle_cmid matches)")
        elif mod_by_django_id:
            correct_name = f'module_{mod_by_django_id.moodle_cmid}'
            print(f"  ✗ {folder_name}: MISMATCH! Should be {correct_name}")
            print(f"    Module: {mod_by_django_id.name} (Django ID={mod_by_django_id.id}, moodle_cmid={mod_by_django_id.moodle_cmid})")
            mismatches[folder_name] = {
                'correct': correct_name,
                'module': mod_by_django_id,
                'path': folder
            }
        else:
            print(f"  ⚠️  {folder_name}: Orphaned (no Module record)")
    
    print("\n" + "="*80)
    if mismatches:
        print(f"❌ Found {len(mismatches)} MISMATCHES - Run fix_module_mpd_urls() then rename_dash_folders()")
    else:
        print("✓ No mismatches found!")
    print("="*80 + "\n")
    
    return mismatches


def fix_module_mpd_urls():
    """
    Update Module.mpd_url to point to correct moodle_cmid-based paths.
    """
    from django.conf import settings
    from frontend.models import Module
    
    print("\n" + "="*80)
    print("🔧 FIXING MODULE MPD_URL MAPPINGS")
    print("="*80)
    
    dash_root = Path(settings.MEDIA_ROOT) / 'dash' if settings.MEDIA_ROOT else None
    if not dash_root or not dash_root.exists():
        print("❌ DASH directory not found")
        return
    
    videos = Module.objects.filter(module_type='video')
    total = 0
    updated = 0
    
    for module in videos:
        total += 1
        
        # Build expected path based on moodle_cmid
        expected_path = dash_root / f'module_{module.moodle_cmid}' / 'stream.mpd'
        expected_url = f'/media/dash/module_{module.moodle_cmid}/stream.mpd'
        
        exists = expected_path.exists()
        
        if exists:
            if module.mpd_url != expected_url:
                print(f"  📝 Updating {module.name} (cmid={module.moodle_cmid})")
                print(f"    Old: {module.mpd_url}")
                print(f"    New: {expected_url}")
                module.mpd_url = expected_url
                module.save()
                updated += 1
            else:
                print(f"  ✓ {module.name} already correct")
        else:
            print(f"  ⚠️  {module.name}: DASH file not found at {expected_path}")
            if module.mpd_url:
                print(f"    Keeping existing: {module.mpd_url}")
    
    print("\n" + "="*80)
    print(f"✓ Updated {updated}/{total} video modules")
    print("="*80 + "\n")


def rename_dash_folders():
    """
    Rename DASH folders from module_<django_id> to module_<moodle_cmid>.
    """
    from django.conf import settings
    from frontend.models import Module
    
    print("\n" + "="*80)
    print("📁 RENAMING DASH FOLDERS")
    print("="*80)
    
    dash_root = Path(settings.MEDIA_ROOT) / 'dash' if settings.MEDIA_ROOT else None
    if not dash_root or not dash_root.exists():
        print("❌ DASH directory not found")
        return
    
    renamed = 0
    errors = 0
    
    for folder in sorted(dash_root.glob('module_*')):
        folder_name = folder.name
        try:
            folder_id = int(folder_name.replace('module_', ''))
        except ValueError:
            continue
        
        mod = Module.objects.filter(id=folder_id).first()
        if not mod:
            continue
        
        # Already correct?
        if folder_id == mod.moodle_cmid:
            print(f"  ✓ {folder_name}: Already correct")
            continue
        
        new_name = f'module_{mod.moodle_cmid}'
        new_path = dash_root / new_name
        
        if new_path.exists():
            print(f"  ⚠️  Cannot rename {folder_name}: {new_name} already exists")
            continue
        
        try:
            folder.rename(new_path)
            print(f"  ✓ Renamed: {folder_name} → {new_name}")
            renamed += 1
        except Exception as e:
            print(f"  ✗ Error renaming {folder_name}: {e}")
            errors += 1
    
    print("\n" + "="*80)
    print(f"✓ Renamed {renamed} folders, {errors} errors")
    print("="*80 + "\n")


def get_dash_status():
    """
    Print status of all video modules and their DASH availability.
    """
    from django.conf import settings
    from frontend.models import Module
    
    print("\n" + "="*80)
    print("📊 DASH STATUS REPORT")
    print("="*80)
    
    videos = Module.objects.filter(module_type='video').order_by('moodle_course_id', 'section_number', 'id')
    
    total = videos.count()
    with_dash = videos.filter(mpd_url__isnull=False).exclude(mpd_url='').count()
    without_dash = total - with_dash
    
    print(f"\nVideo Modules: {total}")
    print(f"  ✓ With DASH: {with_dash}")
    print(f"  ✗ Without DASH: {without_dash}")
    
    print(f"\nDetails:")
    for mod in videos:
        symbol = "✓" if mod.mpd_url else "✗"
        print(f"  {symbol} Django_ID={mod.id:3d} | CMID={mod.moodle_cmid:3d} | {mod.name[:40]:40s}")
        if mod.mpd_url:
            print(f"           → {mod.mpd_url}")
        else:
            expected = f'/media/dash/module_{mod.moodle_cmid}/stream.mpd'
            print(f"           → (missing) expected: {expected}")
    
    print("\n" + "="*80 + "\n")


# ============================================================================
# QUICK REFERENCE: Run these commands in Django shell
# ============================================================================
"""
from pathlib import Path
import os
from django.conf import settings
from frontend.models import Module

# 1. Diagnose issues
print("Step 1: Diagnose problems")
mismatches = diagnose_dash_issues()

# 2. Update Module.mpd_url fields
print("\nStep 2: Update mpd_url fields")
fix_module_mpd_urls()

# 3. Rename folders
print("\nStep 3: Rename folders") 
rename_dash_folders()

# 4. Verify status
print("\nStep 4: Verify status")
get_dash_status()

# Or all at once:
# diagnose_dash_issues()
# fix_module_mpd_urls()
# rename_dash_folders()
# get_dash_status()
"""

if __name__ == '__main__':
    # This allows running via: python manage.py shell < tmp/fix_moodle_dash.py
    print("Import this file in Django shell and run the functions.")
    print("See docstring for usage examples.")
