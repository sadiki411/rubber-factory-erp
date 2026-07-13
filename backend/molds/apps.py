from django.apps import AppConfig
from django.db.backends.signals import connection_created


def configure_sqlite(sender, connection, **kwargs):
    if connection.vendor != "sqlite":
        return
    cursor = connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=20000")
    if connection.settings_dict["NAME"] != ":memory:":
        cursor.execute("PRAGMA journal_mode=WAL")


class MoldsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "molds"

    def ready(self):
        connection_created.connect(configure_sqlite, dispatch_uid="molds.configure_sqlite")
