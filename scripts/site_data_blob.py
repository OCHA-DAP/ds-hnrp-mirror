"""Move the generated site data (site/data/**) between this checkout and blob.

The GitHub Pages deploy used to run scripts/export_site_data.py against the
dev DB from a GitHub runner. The DB is now reachable only through its private
endpoint, so the export runs in the Databricks job (databricks.yml) and the
files are parked on the dev blob; the deploy workflow pulls them from there.
The site itself is unchanged: the same site/data/** files end up on Pages.

    python scripts/site_data_blob.py upload     # Databricks: site/data -> blob
    python scripts/site_data_blob.py download   # GitHub Actions: blob -> site/data

Blob location: dev `projects` container, prefix ds-hnrp-mirror/site-data/ (STAGE env
selects dev/prod, default dev). Upload replaces the whole prefix: files that
no longer exist locally are deleted, so a dropped country disappears from the
site as it did when the export ran in the deploy.
"""

import argparse
import logging
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

import ocha_stratus as stratus  # noqa: E402
from azure.storage.blob import ContentSettings  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("azure").setLevel(logging.WARNING)  # no per-request SDK chatter
logger = logging.getLogger(__name__)

SITE_DATA = Path(__file__).parent.parent / "site" / "data"
CONTAINER = "projects"
PREFIX = "ds-hnrp-mirror/site-data"
STAGE = os.environ.get("STAGE", "dev")


def _blob_name(path: Path) -> str:
    return f"{PREFIX}/{path.relative_to(SITE_DATA).as_posix()}"


def _files(client):
    """Blobs under PREFIX, minus the zero-byte directory markers the storage
    account (hierarchical namespace) creates for each folder."""
    blobs = list(client.list_blobs(name_starts_with=PREFIX + "/", include=["metadata"]))
    names = {b.name for b in blobs}
    return [
        b for b in blobs
        if not (b.metadata or {}).get("hdi_isfolder") == "true"
        and not any(n.startswith(b.name + "/") for n in names)
    ]


def upload():
    files = sorted(p for p in SITE_DATA.rglob("*") if p.is_file())
    if not files:
        raise SystemExit(f"nothing to upload: {SITE_DATA} is empty (run export_site_data.py first)")
    client = stratus.get_container_client(CONTAINER, stage=STAGE, write=True)
    existing = {b.name for b in _files(client)}
    uploaded = set()
    for path in files:
        name = _blob_name(path)
        client.upload_blob(
            name,
            path.read_bytes(),
            overwrite=True,
            content_settings=ContentSettings(content_type="application/json"),
        )
        uploaded.add(name)
    stale = existing - uploaded
    for name in sorted(stale):
        client.delete_blob(name)
    logger.info(
        "uploaded %s files to %s/%s (%s stale removed) at %s",
        len(uploaded), CONTAINER, PREFIX, len(stale),
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def download():
    client = stratus.get_container_client(CONTAINER, stage=STAGE)
    blobs = _files(client)
    if not blobs:
        raise SystemExit(f"no site data under {CONTAINER}/{PREFIX} — has the Databricks job run?")
    shutil.rmtree(SITE_DATA, ignore_errors=True)
    newest = None
    for blob in blobs:
        rel = blob.name[len(PREFIX) + 1 :]
        target = SITE_DATA / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(client.download_blob(blob.name).readall())
        if newest is None or blob.last_modified > newest:
            newest = blob.last_modified
    logger.info("downloaded %s files into %s (blob last modified %s)", len(blobs), SITE_DATA, newest)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action", choices=["upload", "download"])
    args = ap.parse_args()
    upload() if args.action == "upload" else download()


if __name__ == "__main__":
    main()
