from unittest.mock import MagicMock

from django.apps import apps
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import resolve
from ninja.testing import TestClient
from rest_framework.request import Request

from apiary.api import crud, datasets
from apiary.api.permissions import ModelPermissions
from apiary.views import PROJECT_APPS
from bom.models import MortalityBill
from pinkertons.models import Activity, Location


def user(*perms, modules=()):
    return MagicMock(
        is_authenticated=True, is_active=True,
        has_perms=lambda wanted: set(wanted) <= set(perms),
        has_module_perms=lambda label: label in modules,
    )


class ApiRoutingTests(SimpleTestCase):
    def test_every_project_model_gets_crud_under_its_project(self):
        for label in PROJECT_APPS:
            for model in apps.get_app_config(label).get_models():
                # A model named "datasets" would shadow the project's Ninja routes.
                self.assertNotEqual(model._meta.model_name, "datasets")
                for path in (f"/api/v1/{label}/{model._meta.model_name}/",
                             f"/api/v1/{label}/{model._meta.model_name}/1/"):
                    self.assertEqual(resolve(path).func.cls.queryset.model, model, path)

    def test_every_dataset_lives_under_its_project(self):
        paths = datasets.api.get_openapi_schema()["paths"]
        self.assertTrue(paths)
        for path in paths:
            self.assertRegex(path, rf"^/api/v1/({'|'.join(PROJECT_APPS)})/datasets/[a-z-]+/$")
            self.assertEqual(resolve(path).namespace, "datasets", path)

    def test_crud_reads_need_the_view_permission_and_writes_their_own(self):
        # A plain object: on a MagicMock, DRF reads _ignore_model_permissions as truthy.
        view = type("View", (), {"queryset": MortalityBill.objects.all()})()
        factory = RequestFactory()

        def allowed(method, who):
            request = Request(getattr(factory, method)("/"))
            request.user = who
            return ModelPermissions().has_permission(request, view)

        self.assertFalse(allowed("get", AnonymousUser()))
        self.assertFalse(allowed("get", user()))
        self.assertTrue(allowed("get", user("bom.view_mortalitybill")))
        self.assertFalse(allowed("post", user("bom.view_mortalitybill")))
        self.assertTrue(allowed("post", user("bom.add_mortalitybill")))
        self.assertFalse(allowed("delete", user("bom.view_mortalitybill", "bom.change_mortalitybill")))


class DatasetTests(TestCase):
    # Django sets every alias's test database to depend on default.
    databases = {"default", "bom_db", "pinkertons_db"}

    @classmethod
    def setUpTestData(cls):
        MortalityBill.objects.bulk_create([
            MortalityBill(year=1665, type="Plague", count=10),
            MortalityBill(year=1665, type="Plague", count=5),
            MortalityBill(year=1666, type="Fever", count=3),
        ])
        here = Location.objects.create(location_name="Bank", latitude=40.7, longitude=-73.9)
        nowhere = Location.objects.create(location_name="Unmapped")
        Activity.objects.create(investigation="nyc", date="1934-09-22", activity="Surveillance", location=here)
        Activity.objects.create(investigation="nyc", date="1934-09-29", activity="Shadowing", location=nowhere)
        Activity.objects.create(investigation="hobart", date="1936-02-21", activity="Shadowing")

    def setUp(self):
        self.client = TestClient(datasets.api)

    def test_datasets_need_a_permission_in_their_project(self):
        path = "/bom/datasets/deaths-by-year/"
        self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.get(path, user=user(modules={"relec"})).status_code, 401)
        self.assertEqual(self.client.get(path, user=user(modules={"bom"})).status_code, 200)

    def test_time_series_sums_per_year_and_type_with_filters(self):
        bom = user(modules={"bom"})
        self.assertEqual(self.client.get("/bom/datasets/deaths-by-year/", user=bom).json(), [
            {"year": 1665, "type": "Plague", "deaths": 15},
            {"year": 1666, "type": "Fever", "deaths": 3},
        ])
        self.assertEqual(
            self.client.get("/bom/datasets/deaths-by-year/?start=1666", user=bom).json(),
            [{"year": 1666, "type": "Fever", "deaths": 3}],
        )

    def test_monthly_counts_and_joined_points(self):
        pinkertons = user(modules={"pinkertons"})
        self.assertEqual(self.client.get("/pinkertons/datasets/activities-by-month/", user=pinkertons).json(), [
            {"month": "1934-09-01", "investigation": "nyc", "activities": 2},
            {"month": "1936-02-01", "investigation": "hobart", "activities": 1},
        ])
        points = self.client.get("/pinkertons/datasets/activity-points/", user=pinkertons).json()
        self.assertEqual([(p["location"], p["latitude"]) for p in points], [("Bank", 40.7)])
