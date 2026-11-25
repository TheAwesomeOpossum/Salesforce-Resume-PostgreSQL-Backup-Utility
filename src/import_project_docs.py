"""
Import case-study markdown files written by /document-work-project (or the server-side
documentation pass) into the Experience__c staging table.

Each file has YAML frontmatter with `schema: work-project/v1`. The frontmatter supplies the
Experience record fields; the markdown body is the long-form case study used for the portfolio
document and is NOT pushed to Salesforce (only `description_html` is).

Usage:
    # Preview what would be staged (default, no writes):
    PYTHONPATH=. POSTGRES_HOST=localhost POSTGRES_SSLMODE=disable \
        python3 -m src.import_project_docs ~/project-docs/incoming

    # Stage for write-back:
    ... python3 -m src.import_project_docs ~/project-docs/incoming --stage

    # After `python3 -m src.write_back`, copy the new Salesforce IDs back into the files:
    ... python3 -m src.import_project_docs ~/project-docs/incoming --write-ids

Company → Job__c mapping is read from `work_projects.json` in the repository root. That file is
ignored by Git because it holds employer and client names and Job__c record IDs; copy
`work_projects.example.json` to `work_projects.json` and fill in your own values. Files whose
frontmatter already has `salesforce_id` are staged as updates.
"""

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

from src import database as db, salesforce as sf_client
from src.document_project import (
    get_record_type_skills,
    get_valid_skills,
    run as stage_payload,
)
from src.write_back import PERSONAL_EXPERIENCE_JOB_ID

# Employer jobs, the companies filed under each, and their Job__c record IDs are private, so
# they live in work_projects.json (ignored by Git) rather than in this file. Work done for an
# operating company owned by an employer is filed under the employer's job, with Company__c
# naming the operating company.
SETTINGS_FILE = Path(__file__).resolve().parent.parent / "work_projects.json"

JOB_BY_COMPANY = {"Personal": PERSONAL_EXPERIENCE_JOB_ID}
JOB_BY_EMPLOYER_JOB = {"Personal Projects": PERSONAL_EXPERIENCE_JOB_ID}
PARENT_COMPANY_BY_JOB: dict[str, str] = {}
COMPANY_NOTE_BY_JOB: dict[str, str] = {}


def load_settings(path: Path = SETTINGS_FILE) -> None:
    """Fill the mapping tables above from work_projects.json. A missing file is not an error:
    only personal projects can be mapped until it exists."""
    if not path.exists():
        return
    settings = json.loads(path.read_text(encoding="utf-8"))
    for job in settings.get("employer_jobs", []):
        job_id = job["job_id"]
        for company in job.get("companies", []):
            JOB_BY_COMPANY[company] = job_id
        if job.get("employer_job"):
            JOB_BY_EMPLOYER_JOB[job["employer_job"]] = job_id
        if job.get("parent_company"):
            PARENT_COMPANY_BY_JOB[job_id] = job["parent_company"]
        if job.get("operating_company_note"):
            COMPANY_NOTE_BY_JOB[job_id] = job["operating_company_note"]


load_settings()

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def read_frontmatter(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    m = FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError(f"{path.name}: no YAML frontmatter block found")
    data = yaml.safe_load(m.group(1)) or {}
    return data, text


def build_payload(fm: dict, path: Path) -> dict:
    if fm.get("schema") != "work-project/v1":
        raise ValueError(f"{path.name}: schema is {fm.get('schema')!r}, expected 'work-project/v1'")
    for key in ("name", "start_date", "skills", "description_html"):
        if not fm.get(key):
            raise ValueError(f"{path.name}: frontmatter is missing '{key}'")

    company = fm.get("company")
    job_id = (
        fm.get("job_id")
        or JOB_BY_COMPANY.get(company)
        or JOB_BY_EMPLOYER_JOB.get(fm.get("employer_job"))
    )
    if not job_id:
        raise ValueError(
            f"{path.name}: cannot map company {company!r} / employer_job "
            f"{fm.get('employer_job')!r} to a Job__c. Add it to work_projects.json "
            f"(see work_projects.example.json)."
        )

    # `sf_name` is the <=80-char record name; `name` may be a longer headline for the portfolio.
    name = str(fm.get("sf_name") or fm["name"]).strip()
    if len(name) > 80:
        raise ValueError(
            f"{path.name}: name is {len(name)} chars; Experience__c.Name max is 80. "
            f"Add an `sf_name` under 80 chars to the frontmatter."
        )

    # Company__c on Experience__c is a formula (the Job's company), so work done for an operating
    # company under an employer's job is labelled in the description instead.
    description = fm["description_html"]
    note = COMPANY_NOTE_BY_JOB.get(job_id)
    if note and company and company not in ("Personal", PARENT_COMPANY_BY_JOB.get(job_id)):
        description = note.format(company=company) + description

    payload = {
        "name": name,
        "start_date": str(fm["start_date"]),
        "end_date": str(fm["end_date"]) if fm.get("end_date") else None,
        "description": description,
        "skills": list(fm["skills"]),
        "github_url": fm.get("github_url"),
        "job_id": job_id,
        "record_type": fm.get("record_type") or "Work_Experience",
        # Company__c is only meaningful for work filed under an employer job
        "company": company if company and company != "Personal" else None,
    }
    if fm.get("salesforce_id"):
        payload["salesforce_id"] = fm["salesforce_id"]
    return payload


def preflight_skills(sf, payloads: list[dict]) -> dict[str, list[str]]:
    """Return {record_type: [skills missing from that record type]} across all payloads."""
    gvs = get_valid_skills(sf)
    missing: dict[str, set] = {}
    not_in_gvs: set = set()
    for p in payloads:
        rt = p["record_type"]
        rt_skills = get_record_type_skills(sf, rt)
        for sk in p["skills"]:
            if sk not in gvs:
                not_in_gvs.add(sk)
            elif sk not in rt_skills:
                missing.setdefault(rt, set()).add(sk)
    out = {rt: sorted(v) for rt, v in missing.items()}
    if not_in_gvs:
        out["__not_in_global_value_set__"] = sorted(not_in_gvs)
    return out


def write_ids_back(directory: Path, files: list[Path]) -> None:
    """After write_back, look up each file's record by name in PostgreSQL and add salesforce_id."""
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            for path in files:
                fm, text = read_frontmatter(path)
                if fm.get("salesforce_id"):
                    continue
                cur.execute(
                    "SELECT salesforce_id FROM experience WHERE name=%s AND sync_status='synced' "
                    "AND salesforce_id IS NOT NULL ORDER BY id DESC LIMIT 1",
                    (str(fm.get("sf_name") or fm["name"]).strip(),),
                )
                row = cur.fetchone()
                if not row:
                    print(f"  {path.name}: no synced record found yet")
                    continue
                new_text = text.replace("\n---\n", f"\nsalesforce_id: \"{row[0]}\"\n---\n", 1)
                path.write_text(new_text, encoding="utf-8")
                print(f"  {path.name}: salesforce_id = {row[0]}")
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("directory", type=Path, help="Folder of work-project/v1 markdown files")
    ap.add_argument("--stage", action="store_true", help="Stage to PostgreSQL (default is preview only)")
    ap.add_argument("--write-ids", action="store_true", help="Copy synced Salesforce IDs back into frontmatter")
    ap.add_argument("--only", nargs="*", default=None, help="Only these file names")
    args = ap.parse_args()

    files = sorted(p for p in args.directory.glob("*.md") if not p.name.startswith("."))
    if args.only:
        files = [p for p in files if p.name in set(args.only)]
    if not files:
        print(f"No .md files in {args.directory}")
        return 1

    if args.write_ids:
        write_ids_back(args.directory, files)
        return 0

    payloads, errors = [], []
    for path in files:
        try:
            fm, _ = read_frontmatter(path)
            payloads.append((path, build_payload(fm, path), fm))
        except (ValueError, yaml.YAMLError) as exc:
            errors.append(f"{path.name}: {exc}")

    print(f"{len(payloads)} file(s) parsed, {len(errors)} with errors\n")
    for path, p, fm in payloads:
        flags = []
        if fm.get("has_unknowns"):
            flags.append("UNKNOWNS")
        if fm.get("needs_confidentiality_review"):
            flags.append("CONFIDENTIALITY REVIEW")
        action = f"UPDATE {p['salesforce_id']}" if p.get("salesforce_id") else "CREATE"
        print(f"[{action}] {p['name']}")
        print(f"    {p['record_type']} | company={p['company']} | job={p['job_id']} | "
              f"{p['start_date']} → {p['end_date'] or 'Ongoing'}")
        print(f"    skills: {', '.join(p['skills'])}")
        if fm.get("new_skills"):
            print(f"    new_skills (not in picklist yet): {', '.join(fm['new_skills'])}")
        if flags:
            print(f"    flags: {', '.join(flags)}")
        print()
    for e in errors:
        print(f"ERROR {e}")

    sf = sf_client.connect()
    gaps = preflight_skills(sf, [p for _, p, _ in payloads])
    if gaps:
        print("\nSkill picklist gaps — fix before staging:")
        for rt, skills in gaps.items():
            if rt == "__not_in_global_value_set__":
                print(f"  Not in Skills global value set at all: {skills}")
                print("    → run: python3 scripts/add_skills_to_recordtype.py <RecordType> "
                      + " ".join(repr(s) for s in skills) + "   (adds to the value set too)")
            else:
                print(f"  Missing from record type {rt}: {skills}")
                print(f"    → run: python3 scripts/add_skills_to_recordtype.py {rt} "
                      + " ".join(repr(s) for s in skills))
        if args.stage:
            print("\nNot staging while gaps exist.")
            return 1

    if not args.stage:
        print("\nPreview only. Re-run with --stage to stage these records.")
        return 0

    staged = 0
    for path, p, _ in payloads:
        try:
            stage_payload(p)
            staged += 1
        except ValueError as exc:
            print(f"ERROR staging {path.name}: {exc}")
    print(f"\nStaged {staged}/{len(payloads)}. Next: PYTHONPATH=. POSTGRES_HOST=localhost "
          f"POSTGRES_SSLMODE=disable python3 -m src.write_back")
    return 0 if staged == len(payloads) else 1


if __name__ == "__main__":
    sys.exit(main())
