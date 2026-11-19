"""SQLite cannot express interval exclusion as a CHECK/UNIQUE constraint.

Guard the interval at the DB boundary as well as in the transactional command.
Legacy NULL ranges are untouched. Current ERP supports SQLite only.
"""
from django.db import migrations


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "sqlite":
        raise RuntimeError("备卡模段约束尚未实现此数据库方言，不能无保护地启用。")
    for action in ("INSERT", "UPDATE"):
        schema_editor.execute(f"""
            CREATE TRIGGER fc_no_overlap_{action.lower()}
            BEFORE {action} ON flowcards_cardgroup
            WHEN NEW.run_id IS NOT NULL AND NEW.range_start IS NOT NULL
            BEGIN
              SELECT RAISE(ABORT, 'fc_overlapping_mold_range')
              WHERE EXISTS (SELECT 1 FROM flowcards_cardgroup g
                WHERE g.run_id = NEW.run_id AND g.id != NEW.id
                  AND g.range_start IS NOT NULL
                  AND g.range_start <= NEW.range_end AND g.range_end >= NEW.range_start);
            END
        """)


def remove(apps, schema_editor):
    for action in ("insert", "update"):
        schema_editor.execute(f"DROP TRIGGER IF EXISTS fc_no_overlap_{action}")


class Migration(migrations.Migration):
    dependencies = [("flowcards", "0003_preparation_context")]
    operations = [migrations.RunPython(install, remove)]
