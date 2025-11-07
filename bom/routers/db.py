class DatabaseRouter:

    APP_LABEL = "bom"
    DB_NAME = f"{APP_LABEL}_db"

    def db_for_read(self, model, **hints):
        if model._meta.app_label == self.APP_LABEL:
            return self.DB_NAME
        return None

    def db_for_write(self, model, **hints):
        if model._meta.app_label in self.APP_LABEL:
            return self.DB_NAME
        return None

    def allow_relation(self, obj1, obj2, **hints):
        if (
            obj1._meta.app_label == self.APP_LABEL
            or obj2._meta.app_label == self.APP_LABEL
        ):
            return True
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label == self.APP_LABEL:
            if db == self.DB_NAME:
                # only handle migrations for this app for this db
                return True
            else:
                # this app only migrates on its designated db
                return False
        return None
