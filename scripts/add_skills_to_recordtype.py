#!/usr/bin/env python3
"""
Add Skills__c picklist values to an Experience__c record type (and to the Skills global value
set if absent), then deploy both together.

Why retrieve first: the local RecordType XML in LWRPortfolioWebsite is stale relative to the org
(e.g. Work_Experience has 160 values locally but 170 in the org). Deploying a stale file would
REMOVE values from the record type, so this script always pulls the live metadata first.

Usage:
    python3 scripts/add_skills_to_recordtype.py Work_Experience "PostgreSQL" "React" [--no-deploy]
"""

import argparse
import os
import re
import subprocess
import sys
from html import escape
from pathlib import Path

from dotenv import load_dotenv

PROJECT = Path.home() / "Salesforce_Projects/LWRPortfolioWebsite"
# The org username is read from SF_USERNAME in the repository's ignored .env file.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
ORG = os.environ.get("SF_USERNAME")
if not ORG:
    sys.exit("SF_USERNAME is not set. Add it to .env (see .env.example).")
RT_DIR = PROJECT / "force-app/main/default/objects/Experience__c/recordTypes"
GVS_FILE = PROJECT / "force-app/main/default/globalValueSets/Skills.globalValueSet-meta.xml"


def sf(*args: str) -> None:
    cmd = ["sf", *args, "--target-org", ORG]
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=PROJECT, check=True)


SKILLS_BLOCK_RE = re.compile(r"<picklistValues>\s*<picklist>Skills__c</picklist>.*?</picklistValues>", re.DOTALL)


def skills_block(rt_xml: str) -> re.Match:
    """The <picklistValues> block for Skills__c; a record type file holds one block per picklist."""
    m = SKILLS_BLOCK_RE.search(rt_xml)
    if not m:
        raise SystemExit("No <picklistValues> block for Skills__c found in the record type XML")
    return m


def existing_values(xml: str, tag: str) -> set[str]:
    return set(re.findall(rf"<{tag}>\s*<fullName>([^<]*)</fullName>", xml))


def insert_sorted(xml: str, block_tag: str, items: list[str], make_block) -> str:
    """Insert blocks alphabetically among the existing <block_tag> entries in `xml`."""
    pattern = re.compile(rf"(\s*)<{block_tag}>\s*<fullName>([^<]*)</fullName>.*?</{block_tag}>", re.DOTALL)
    matches = list(pattern.finditer(xml))
    if not matches:
        raise SystemExit(f"No <{block_tag}> entries found to anchor insertion")
    indent = matches[0].group(1)
    for item in items:
        name = escape(item, quote=True)
        block = indent + make_block(name).replace("\n", indent)
        anchor = next((m for m in matches if m.group(2).lower() > name.lower()), None)
        if anchor:
            xml = xml[:anchor.start()] + block + xml[anchor.start():]
        else:
            last = matches[-1]
            xml = xml[:last.end()] + block + xml[last.end():]
        matches = list(pattern.finditer(xml))
    return xml


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("record_type", help="DeveloperName, e.g. Work_Experience")
    ap.add_argument("skills", nargs="+")
    ap.add_argument("--no-deploy", action="store_true")
    args = ap.parse_args()

    rt_file = RT_DIR / f"{args.record_type}.recordType-meta.xml"

    # Retrieving a RecordType on its own returns NO picklist values (just name/label/active);
    # the values only come back when the parent CustomObject is retrieved with it.
    print("Retrieving live metadata so we never deploy a stale picklist...")
    sf("project", "retrieve", "start",
       "--metadata", "CustomObject:Experience__c",
       "--metadata", "GlobalValueSet:Skills")

    gvs_xml = GVS_FILE.read_text()
    rt_xml = rt_file.read_text()
    gvs_have = existing_values(gvs_xml, "customValue")
    sk = skills_block(rt_xml)
    rt_have = existing_values(sk.group(0), "values")

    add_gvs = [s for s in args.skills if escape(s, quote=True) not in gvs_have]
    add_rt = [s for s in args.skills if escape(s, quote=True) not in rt_have]
    print(f"Add to global value set: {add_gvs or 'nothing'}")
    print(f"Add to {args.record_type}: {add_rt or 'nothing'}")
    if not add_gvs and not add_rt:
        return 0

    if add_gvs:
        gvs_xml = insert_sorted(
            gvs_xml, "customValue", add_gvs,
            lambda n: f"<customValue>\n    <fullName>{n}</fullName>\n    <default>false</default>\n    <label>{n}</label>\n</customValue>",
        )
        GVS_FILE.write_text(gvs_xml)
    if add_rt:
        new_block = insert_sorted(
            sk.group(0), "values", add_rt,
            lambda n: f"<values>\n    <fullName>{n}</fullName>\n    <default>false</default>\n</values>",
        )
        rt_xml = rt_xml[:sk.start()] + new_block + rt_xml[sk.end():]
        rt_file.write_text(rt_xml)

    if args.no_deploy:
        print("Files edited; skipping deploy (--no-deploy).")
        return 0
    sf("project", "deploy", "start",
       "--metadata", "GlobalValueSet:Skills",
       "--metadata", f"RecordType:Experience__c.{args.record_type}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
