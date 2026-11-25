# /document-work-project

Portable, offline version of `/document-project`. Turns the current Claude Code conversation into a
reviewed case-study file on this computer. It does **not** connect to Salesforce, PostgreSQL, or any
server. The files it writes are imported later on the home server, where they become `Experience__c`
records and source material for the capability portfolio.

Optional argument: a project name or a hint about which part of the conversation to document
(for example `/document-work-project quoting cleanup`).

Install: copy this file to `~/.claude/commands/document-work-project.md` on the computer where the
work conversations live. Run it inside the conversation you want to document.

---

## Ground rules

- Write only to the output folder below. Do not push, deploy, commit, or send anything anywhere.
- Never copy secrets into the file: passwords, API keys, tokens, connection strings, session IDs.
  Describe them generically ("an API key stored in Named Credentials").
- Do not include customer names, customer contact details, employee personal data, or exact
  financial figures of the company unless the user explicitly says to. Use roles and ranges instead
  ("a regional sales manager", "several hundred open quotes").
- Record only what the conversation, the repository, or the user supports. If a result or number is
  not known, write `UNKNOWN - ask` instead of estimating. Do not invent metrics.
- The reader of the final portfolio is a mix of non-technical leadership and technical managers.
  Every section must make sense to someone who has never used the technology involved.

---

## Step 1: Settle the output folder and the company

Output folder: `~/project-docs/` (create it if missing). If `~/project-docs/.config.json` exists and
contains `"output_dir"`, use that path instead; the user may point it at an Obsidian vault folder.

Determine which company the work was for. The valid company names and the employer job they
belong to are private, so they are not written in this file. Read them from
`~/project-docs/.config.json`, which uses the same layout as `work_projects.example.json` in the
import repository:

- each entry under `employer_jobs` has a `companies` list: those are the valid company values
- its `parent_company` is the value to use for work done for the parent company or across
  several of its companies
- its `employer_job` is the value for the `employer_job` line in Step 7
- anything else the user states is also valid

If `~/project-docs/.config.json` does not exist or has no `employer_jobs`, ask the user for the
company and the employer job title. If the conversation does not make the company obvious, ask
the user once before continuing.

---

## Step 2: Gather the evidence

Use these sources, in this order:

1. **The current conversation.** This is the primary source. Extract: the original problem as the
   user first described it, what was tried, dead ends, the root cause, what was built or changed,
   who the work was for, and any stated result.

2. **The saved transcript, if the conversation has been summarized.** Long conversations get
   compacted, which drops early detail such as the original problem statement. The full transcript
   is a `.jsonl` file under `~/.claude/projects/<folder named after the working directory>/`.
   Find the most recently modified one. Do not read it whole. Search it for the earliest user
   messages and for words such as `error`, `root cause`, `fixed`, `works`, `deployed`, then read
   only the matching parts.

3. **The working directory.** `git log --reverse --format='%ad %s' --date=short | head -40` for the
   timeline and first commit date; `README.md`, `CLAUDE.md`, and any memory or notes files for
   context. If there is no git history, use the oldest modification date among the key project
   files as the start date.

4. **Notes the user points to**, such as Obsidian notes or memory files about the company. Ask the
   user whether any exist only if the conversation has gaps you cannot fill.

Then check `~/project-docs/` for an existing file about the same project. If one exists, this run is
an **update**: read it, keep what is still true, and add what is new. Do not create a second file
for the same project.

---

## Step 3: Decide whether this is one project or several

A long conversation often covers more than one piece of work. A project is one problem with one
outcome. If the conversation contains several, list them for the user with one line each and ask
which to document now. Write one file per project.

---

## Step 4: Draft the case study

Fill in this structure. Plain words, past tense for finished work, present tense for ongoing work.

```
PROJECT DRAFT  [NEW | UPDATE]
Name:        {what was done, as an outcome: "Cut quote entry time by automating takeoff pricing"}
Company:     {from Step 1}
Dates:       {YYYY-MM-DD} to {YYYY-MM-DD or Ongoing}
Status:      {Complete | Ongoing | Paused}
Category:    {Salesforce | AI and automation | Integration | Data | Infrastructure and networking |
              Hardware and devices | Process and training | Other}
Skills:      {5-10, see Step 5}

One-line summary
  {One sentence a company owner would understand and care about.}

The problem
  {What was going wrong, who it affected, and what it was costing in time, money, errors, or risk.
   Use the user's original description where possible.}

How I broke it down
  {The diagnosis. What was unknown at the start, how the problem was split into smaller questions,
   what was ruled out and how, and what the actual cause turned out to be. This is the most
   important section: it is the evidence of problem-solving ability, independent of technology.
   Include dead ends that taught something.}

What I built or changed
  {The fix, described by what it does. Name technologies once, with a few words on what each is
   for. Note anything learned on the job for this project.}

Result
  {What is different now. Numbers if known (hours saved per week, error rate, records processed,
   people unblocked). Mark each unknown figure `UNKNOWN - ask`.}

Who was involved
  {Roles only: who asked for it, who uses it, who else worked on it. State clearly what the user
   did personally versus what others did.}

Technical detail (for technical readers)
  {Architecture, key design decisions and the alternatives rejected, scale, constraints.
   A short paragraph or a few bullets.}

Open items
  {What is not finished, known limitations, next steps.}

Questions for the user
  {Every `UNKNOWN - ask` gathered in one list, plus anything else needed to finish the write-up.}

Needs review before sharing
  {Anything that may be confidential or sensitive and should be checked before this leaves the
   company: system names, vendor contracts, pricing logic, security details. Write "None found"
   if nothing applies.}
```

---

## Step 5: Skills

Pick the 5-10 skills that mattered most. Do not pad. Where a skill matches an entry in the list
below, use that exact spelling and capitalization, because the list is the Salesforce picklist the
record will be loaded into. Put any skill that is not in the list under `new_skills` instead; it
will be added to the picklist at import time.

Accounting Seed, AI, Agentforce, Altium, Analog Circuit Design, Analytics Cloud, Android, Apex,
App Cloud, Arduino, Art, ASIC, Aura, Azure, Bandsaw, Bash, Batch Apex, Bike, BitBucket, C++,
Change Data Capture, ChatGPT, Claude, Claude API, Claude Code, Cloudflare, CNC, CodeServer,
Commerce Cloud, Community Builder, Containerization, Copado, CPQ, CSS, Custom DNS, Deepseek,
Dell Boomi, Digital Circuit Design, Docker, Docker Compose, Docker LXC, DocuSign, Dremel,
Drill Press, Drone, Drone Piloting, e-Bike, Embedded Systems, ESP8266, Experience Cloud, FastAPI,
Field Service Lightning, Firewall, Flow Builder, FPGA, Frigate, Fusion 360, Git, Google Coral,
Google Firebase, Google Search API, Hardware Design, Hardwiring, Helm, Hexacopter,
High Availability, Home Assistant, HomeLab, Home Lab, HTML, Hypervisor, Instructor,
Integration Cloud, Invocable Apex, iOS, Java, JavaScript, Jira, Jitterbit, JSON, Kubernetes, Lathe,
Let's Encrypt, Lightning Web Components, Link Budget, Longhorn, Marketing Cloud, Maven,
Mechanical Engineering, Mechanics, Mill, MQTT, Multi-Meter, Network Engineering, Networking, NVR,
Object Detection, Objective-C, Obsidian, Ollama, On Site Installation, Oscilloscope, PCB Design,
Persistent Storage, PFSense, pgvector, Photography, PID Tuning, Pilot, Polishing, Portainer,
PostgreSQL, Process Builder, Proxmox, Public Service, Python, Quadcopter, Queueable Apex, RADAR,
Radio Frequency Engineering, RAID, Rancher, Rapid Prototyping, Raspberry Pi, React, REST,
Restoration, Reverse-Proxy, RF Engineering, RKE2, Router, Sales Cloud, Salesforce Administration,
Salesforce Classic, Salesforce CLI, Sanding, Scheduled Apex Jobs, Schematic Planning, Screen Flows,
Search and Rescue, Serper.dev, Service Cloud, SF Classic, SF Lightning, Site Survey, Slack,
Small Batch Manufacturing, Small Business, Smith Chart, Soldering, SolidWorks, SOQL, SOSL,
Spectrum Analyzer, SpringBoot, SQL, SSL Certification, Subnet, Subnetting, Surface Mount Soldering,
Table Saw, Tensorflow, TPU, Traefik, Transmission Line Model, TrueNAS, TrueNAS Scale, Twitch,
TypeScript, UAS, Ubuntu Server, uLogo, Verilog, Video Production, Virtualization, Visualforce,
Visual Studio Code, VS Code, WebDAV, XactWare, XML, ZFS

---

## Step 6: User review

Show the full draft, then ask:

```
Review the draft above.
- Answer any of the "Questions for the user" to fill in the unknowns
- Edit a section: "edit [section]: [new text]"
- Approve: "approve"
- Cancel: "cancel"
```

Apply answers and edits, show the revised draft, and repeat until the user types "approve" or
"cancel". Write nothing to disk before approval. On cancel, stop.

Unknowns the user cannot answer stay in the file as `UNKNOWN - ask`; they can be filled in later.

---

## Step 7: Write the file

Path: `{output folder}/{company-slug}--{project-slug}.md`, lowercase with hyphens, for example
`~/project-docs/example-company-a--takeoff-pricing-automation.md`. On an update, overwrite the existing
file and bump `updated`.

Write exactly this layout. The frontmatter is read by the import script, so keep the keys and types
as shown. `description_html` is the short version for the Salesforce record: 2-4 sentences in `<p>`
tags covering the problem, the fix, and the result, with no confidential detail.

```markdown
---
schema: work-project/v1
name: "..."
company: "..."              # the company from Step 1
employer_job: "..."         # the matching employer_job from Step 1
record_type: Work_Experience
start_date: YYYY-MM-DD
end_date: YYYY-MM-DD        # or null when ongoing
status: Complete            # Complete | Ongoing | Paused
category: "..."
skills: ["Apex", "SOQL"]
new_skills: []
has_unknowns: true          # true if any "UNKNOWN - ask" remains
needs_confidentiality_review: false
created: YYYY-MM-DD
updated: YYYY-MM-DD
source_session: "..."       # transcript file name, if found
description_html: "<p>...</p>"
---

# {name}

**One-line summary:** ...

## The problem
## How I broke it down
## What I built or changed
## Result
## Who was involved
## Technical detail
## Open items
## Questions still open
## Needs review before sharing
```

---

## Step 8: Confirm

Report:

```
Saved: {full path}
Status: {NEW | UPDATED}   Unknowns remaining: {count}   Confidentiality review needed: {yes/no}
```

Then list every file now in the output folder with its company and status, so the user can see
what has been documented so far and what is left. Remind the user that these files stay on this
computer until they copy the folder to the home server for import.
