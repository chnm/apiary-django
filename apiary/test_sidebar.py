from django.contrib.auth.models import Group, Permission, User
from django.test import TestCase


class SidebarTests(TestCase):
    databases = "__all__"

    def test_models_nest_under_data_models_and_open_on_their_page(self):
        self.client.force_login(User.objects.create_superuser("root", password="x"))
        page = self.client.get("/admin/").content.decode()
        self.assertIn("Data Models", page)
        self.assertIn('href="/admin/bom/mortalitybill/"', page)
        self.assertIn('href="/admin/pinkertons/location/"', page)
        self.assertIn("subnavigationOpen", page)  # Unfold's collapsible sub-navigation
        self.assertIn("subnavigationOpen': false", page)  # closed away from the models

        on_model = self.client.get("/admin/pinkertons/location/").content.decode()
        self.assertIn("subnavigationOpen': true", on_model)

    def test_a_project_member_sees_only_that_projects_models(self):
        viewer = Group.objects.create(name="bom-viewer")
        viewer.permissions.add(Permission.objects.get(codename="view_mortalitybill"))
        user = User.objects.create_user("reader", is_staff=True)
        user.groups.add(viewer)
        self.client.force_login(user)

        page = self.client.get("/admin/").content.decode()
        self.assertIn('href="/admin/bom/mortalitybill/"', page)
        self.assertNotIn('href="/admin/pinkertons/location/"', page)
        self.assertNotIn("/admin/apiary/site/?project__exact=bom", page)  # Sites: superusers only

    def test_the_current_page_is_highlighted(self):
        # Function links were never marked active: Unfold compares the link first.
        import re

        self.client.force_login(User.objects.create_superuser("root", password="x"))
        page = self.client.get("/admin/auth/user/").content.decode()
        anchor = re.search(r'<a href="/admin/auth/user/"[^>]*>', page).group(0)
        self.assertIn("active", anchor)
