from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('frontend', '0011_rename_cmid_module_moodle_cmid_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='module',
            name='section_number',
            field=models.IntegerField(blank=True, null=True, help_text='Moodle section number (sectionnum)'),
        ),
    ]

