class AdminRouter:

    route_app_labels = {
        "admin",
        "auth",
        "contenttypes",
        "sessions"
    }

    def db_for_read(self, model, **hints):
        if model._meta.app_label in self.route_app_labels:
            return "default"
        return None

    def db_for_write(self, model, **hints):
        if model._meta.app_label in self.route_app_labels:
            return "default"
        return None

    def allow_relation(self, obj1, obj2, **hints):
        if (
            obj1._meta.app_label in self.route_app_labels
            or obj2._meta.app_label in self.route_app_labels
        ):
            return True
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label in self.route_app_labels:
            if db == "default":
                return True
            else:
                return False
        return None

class DefaultRouter:
    def db_for_read(self, model, **hints):
        return "default"

    def db_for_write(self, model, **hints):
        return "default"

    def allow_relation(self, obj1, obj2, **hints):
        db_set = {"default"}
        if obj1._state.db in db_set and obj2._state.db in db_set:
            return True
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if db == "default":
            return True

class AbstractRouter:

    app_label = None
    db_name = None

    def __init__(self, app_label=None, db_name=None):
        if app_label:
            self.app_label = app_label
        if db_name:
            self.db_name = db_name

    def db_for_read(self, model, **hints):
        if model._meta.app_label == self.app_label:
            return self.db_name
        return None

    def db_for_write(self, model, **hints):
        if model._meta.app_label in self.app_label:
            return self.db_name
        return None

    def allow_relation(self, obj1, obj2, **hints):
        if (
            obj1._meta.app_label == self.app_label
            or obj2._meta.app_label == self.app_label
        ):
            return True
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label == self.app_label:
            if db == self.db_name:
                # only handle migrations for this app for this db
                return True
            else:
                # this app only migrates on its designated db
                return False
        return None

class BomRouter(AbstractRouter):
    def __init__(self):
        super().__init__(
            app_label='bom',
            db_name='bom_db'
        )
class ConnThreadsRouter(AbstractRouter):
    def __init__(self):
        super().__init__(
            app_label='connthreads',
            db_name='connthreads_db'
        )
class MappingViolenceRouter(AbstractRouter):
    def __init__(self):
        super().__init__(
            app_label='mappingviolence',
            db_name='mappingviolence_db'
        )
class RelecRouter(AbstractRouter):
    def __init__(self):
        super().__init__(
            app_label='relec',
            db_name='relec_db'
        )
