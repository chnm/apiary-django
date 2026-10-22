"""
Copy the whole object-storage bucket to a directory and back, so restic holds
it beside the PostgreSQL dump and SQLite files (k8s/kustomization.yaml, the
postgres-backup CronJob) and a teardown loses nothing (k8s/restore.yaml).

    python -m apiary.bucket dump /dump/bucket       nightly, before restic
    python -m apiary.bucket check /restore/dump/bucket    restore rehearsal
    python -m apiary.bucket restore /restore/dump/bucket  real recovery

dump writes <dir>.manifest.json (every key and size) beside <dir>; check and
restore read it. Credentials and bucket come from the app's OBJ_STORAGE_*
environment. Plain boto3, no Django, so the backup runs without a database.
"""
import json
import os
import shutil
import sys
from pathlib import Path

# Leave room on the volume for the dump and SQLite files beside the bucket.
FREE_SPACE_MARGIN = 0.9


def client():
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=os.environ["OBJ_STORAGE_ENDPOINT_URL"],
        region_name=os.environ.get("OBJ_STORAGE_REGION") or None,
        aws_access_key_id=os.environ["OBJ_STORAGE_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["OBJ_STORAGE_SECRET_ACCESS_KEY"],
        # Garage serves dotted bucket names only with path-style addressing.
        config=Config(s3={"addressing_style": "path"}),
    )


def manifest_path(root):
    return Path(f"{root}.manifest.json")


def local_path(root, key):
    """The file for an object key, refusing keys that would land outside root."""
    target = (root / key).resolve()
    if root.resolve() not in target.parents:
        raise SystemExit(f"[bucket] refusing object key outside the backup: {key!r}")
    return target


def dump(root, s3=None, bucket=None):
    s3 = s3 or client()
    bucket = bucket or os.environ["OBJ_STORAGE_BUCKET_NAME"]
    root = Path(root)
    objects = [
        {"key": obj["Key"], "size": obj["Size"]}
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket)
        for obj in page.get("Contents", [])
        if not obj["Key"].endswith("/")
    ]
    total = sum(obj["size"] for obj in objects)
    root.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(root).free
    if total > free * FREE_SPACE_MARGIN:
        raise SystemExit(f"[bucket] {bucket} holds {total} bytes but only {free} are free here; "
                         "raise the backup's dump volume.")
    for obj in objects:
        target = local_path(root, obj["key"])
        target.parent.mkdir(parents=True, exist_ok=True)
        s3.download_file(bucket, obj["key"], str(target))
        if target.stat().st_size != obj["size"]:
            raise SystemExit(f"[bucket] {obj['key']}: got {target.stat().st_size} bytes, expected {obj['size']}")
    manifest_path(root).write_text(json.dumps({"bucket": bucket, "objects": objects}, indent=1))
    print(f"[bucket] {bucket}: {len(objects)} objects, {total} bytes copied")
    return objects


def check(root):
    root = Path(root)
    manifest = json.loads(manifest_path(root).read_text())
    problems = []
    for obj in manifest["objects"]:
        target = local_path(root, obj["key"])
        if not target.is_file():
            problems.append(f"missing {obj['key']}")
        elif target.stat().st_size != obj["size"]:
            problems.append(f"{obj['key']}: {target.stat().st_size} bytes, expected {obj['size']}")
    if problems:
        raise SystemExit("[bucket] restored copy does not match its manifest:\n  " + "\n  ".join(problems[:20]))
    print(f"[bucket] {manifest['bucket']}: {len(manifest['objects'])} objects match the manifest")
    return manifest


def restore(root, s3=None, bucket=None):
    """Upload every object in the manifest. Objects the bucket holds beyond it are left alone."""
    manifest = check(root)
    s3 = s3 or client()
    bucket = bucket or os.environ["OBJ_STORAGE_BUCKET_NAME"]
    for obj in manifest["objects"]:
        s3.upload_file(str(local_path(Path(root), obj["key"])), bucket, obj["key"])
    print(f"[bucket] {bucket}: {len(manifest['objects'])} objects restored")


if __name__ == "__main__":
    commands = {"dump": dump, "check": check, "restore": restore}
    if len(sys.argv) != 3 or sys.argv[1] not in commands:
        raise SystemExit(f"usage: python -m apiary.bucket {{{'|'.join(commands)}}} DIR")
    commands[sys.argv[1]](sys.argv[2])
