from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('frontend', '0012_module_section_number'),
    ]

    operations = [
        migrations.AddField(
            model_name='module',
            name='moodle_module_type',
            field=models.CharField(blank=True, help_text='Actual Moodle module name (page, resource, quiz, assign)', max_length=50, null=True),
        ),
        migrations.AlterField(
            model_name='module',
            name='module_type',
            field=models.CharField(help_text='Section type e.g., video, theory, quiz, checkpoint', max_length=50),
        ),
    ]

