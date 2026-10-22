import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase

from apiary import bucket


class FakeS3:
    """Just the client calls apiary.bucket makes, over an in-memory bucket."""

    def __init__(self, objects):
        self.objects = dict(objects)
        self.uploaded = {}

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        fake = self

        class Paginator:
            def paginate(self, Bucket):
                keys = sorted(fake.objects)
                for start in range(0, len(keys), 2):  # several pages
                    yield {"Contents": [{"Key": k, "Size": len(fake.objects[k])} for k in keys[start:start + 2]]}
                yield {}  # an empty page has no Contents

        return Paginator()

    def download_file(self, bucket_name, key, filename):
        Path(filename).write_bytes(self.objects[key])

    def upload_file(self, filename, bucket_name, key):
        self.uploaded[key] = Path(filename).read_bytes()


class BucketBackupTests(SimpleTestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp()) / "bucket"

    def test_dump_check_and_restore_round_trip(self):
        source = {
            "private/sites/bom/k/v1/index.html": b"<h1>hi</h1>",
            "private/snapshots/bom/k/data.json.gz": b"\x1f\x8b data",
            "public/logo.png": b"png",
            "private/": b"",  # a folder marker, not an object to keep
        }
        objects = bucket.dump(self.root, s3=FakeS3(source), bucket="b")
        self.assertEqual(len(objects), 3)
        self.assertEqual((self.root / "public/logo.png").read_bytes(), b"png")
        manifest = json.loads(Path(f"{self.root}.manifest.json").read_text())
        self.assertEqual(manifest["bucket"], "b")
        self.assertIn({"key": "private/sites/bom/k/v1/index.html", "size": 11}, manifest["objects"])

        bucket.check(self.root)
        target = FakeS3({})
        bucket.restore(self.root, s3=target, bucket="b")
        self.assertEqual(target.uploaded, {k: v for k, v in source.items() if not k.endswith("/")})

    def test_check_catches_missing_and_changed_files(self):
        bucket.dump(self.root, s3=FakeS3({"a.txt": b"aaa", "b.txt": b"bbb"}), bucket="b")
        (self.root / "a.txt").unlink()
        (self.root / "b.txt").write_bytes(b"b")
        with self.assertRaises(SystemExit) as error:
            bucket.check(self.root)
        self.assertIn("missing a.txt", str(error.exception))
        self.assertIn("b.txt: 1 bytes, expected 3", str(error.exception))
        with self.assertRaises(SystemExit):
            bucket.restore(self.root, s3=FakeS3({}), bucket="b")  # nothing uploaded from a bad copy

    def test_refuses_keys_outside_the_backup(self):
        for key in ("../escape.txt", "a/../../escape.txt", "/etc/passwd"):
            with self.assertRaisesRegex(SystemExit, "outside the backup"):
                bucket.dump(self.root, s3=FakeS3({key: b"x"}), bucket="b")

    def test_refuses_a_bucket_larger_than_the_free_space(self):
        with patch("apiary.bucket.shutil.disk_usage", return_value=type("U", (), {"free": 10})()):
            with self.assertRaisesRegex(SystemExit, "holds 100 bytes but only 10 are free"):
                bucket.dump(self.root, s3=FakeS3({"big": b"x" * 100}), bucket="b")
