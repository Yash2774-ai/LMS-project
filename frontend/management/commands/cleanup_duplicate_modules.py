from django.core.management.base import BaseCommand
from django.db import models, transaction

from frontend.models import Module


class Command(BaseCommand):
    help = (
        "Remove duplicate Module rows that share the same moodle_cmid. "
        "Keeps the record with an mpd_url if available; otherwise keeps the most recently updated row."
    )

    def handle(self, *args, **options):
        duplicates = (
            Module.objects.values("moodle_cmid")
            .annotate(count=models.Count("id"))
            .filter(count__gt=1)
        )

        if not duplicates:
            self.stdout.write(self.style.SUCCESS("No duplicate modules found."))
            return

        total_deleted = 0

        for dup in duplicates:
            cmid = dup["moodle_cmid"]
            with transaction.atomic():
                candidates = (
                    Module.objects.filter(moodle_cmid=cmid)
                    .annotate(
                        has_mpd=models.Case(
                            models.When(mpd_url__isnull=False, then=models.Value(1)),
                            default=models.Value(0),
                            output_field=models.IntegerField(),
                        )
                    )
                    .order_by("-has_mpd", "-updated_at", "-id")
                )

                keeper = candidates.first()
                if not keeper:
                    continue

                to_delete = candidates.exclude(id=keeper.id)
                delete_ids = list(to_delete.values_list("id", flat=True))
                deleted_count, _ = to_delete.delete()
                total_deleted += deleted_count

                self.stdout.write(
                    self.style.WARNING(
                        f"cmid {cmid}: kept id={keeper.id} "
                        f"(mpd_url={'set' if keeper.mpd_url else 'empty'}), "
                        f"deleted ids={delete_ids}"
                    )
                )

        self.stdout.write(
            self.style.SUCCESS(f"Cleanup complete. Total duplicate rows deleted: {total_deleted}")
        )
