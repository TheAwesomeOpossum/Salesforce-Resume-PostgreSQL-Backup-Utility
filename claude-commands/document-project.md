# /document-project

Documents the current project as an Experience__c record in Salesforce via the AI Project Documentation Pipeline.

This command reads conversation context, generates an Experience record draft, waits for your approval, then stages and pushes it to Salesforce.

---

## Step 1: Read Context

Gather project information from the following sources (in priority order):

1. **`.opossum.json`** — If the file exists in the current working directory, read it. Fields in `.opossum.json` override inferred values:
   - `name` → record name
   - `start_date` → ISO date string
   - `end_date` → ISO date string or null
   - `github_url` → public repo URL
   - `description_override` → if non-empty, use verbatim as description (skip generation)
   - `skills_override` → if non-empty list, use verbatim as skills (skip inference)

2. **Current conversation** — Extract: project name, technologies used, what was accomplished, start date, any GitHub repo mentions, key architectural decisions.

3. **`git log --oneline -20`** in the current directory — Use the earliest commit date to infer `start_date` if not otherwise available. If `git log` returns nothing (not a git repo or empty history), fall back to:
   ```bash
   stat README.md docker-compose.yml docker-compose.yaml Dockerfile pyproject.toml package.json 2>/dev/null \
     | grep -E "Birth|Modify" | sort | head -1
   ```
   Use the earliest Birth or Modify timestamp found across key project files as the `start_date`.

4. **`README.md`** in the current directory (if it exists) — Supplementary project context.

---

## Step 2: Select Skills

Read the valid skill values from:
`~/Salesforce_Projects/LWRPortfolioWebsite/force-app/main/default/globalValueSets/Skills.globalValueSet-meta.xml`

Match project technologies to the exact `<fullName>` values in the XML. Matching is case-sensitive and must be exact (e.g., `"Python"` not `"python"`; `"Docker Compose"` not `"Docker-Compose"`).

Select the 5–10 most relevant skills. Do not pad with loosely-related skills.

**Before finalizing the skills list — cross-check against the RecordType:**

Read `force-app/main/default/objects/Experience__c/recordTypes/Personal_Experience.recordType-meta.xml` and verify every selected skill appears in its `<picklistValues>` block. A skill can exist in the GlobalValueSet but be absent from a RecordType's allowed list — Salesforce will reject it with `INVALID_OR_NULL_FOR_RESTRICTED_PICKLIST` even though the GVS describe call shows it as valid.

For any selected skill missing from the RecordType's `<picklistValues>`, treat it the same as a new skill (see below): add it to the RecordType XML and deploy before proceeding.

**If a technology has no match in the XML:**
Present it as a proposed new skill: "The technology '{X}' is not in the Skills value set. Add it? [y/n]"
- If yes: Edit both files — the global value set and the `Personal_Experience` Record Type — then deploy them together:

  **1. Add to global value set** (`force-app/main/default/globalValueSets/Skills.globalValueSet-meta.xml`):
  ```xml
  <customValue>
      <fullName>New Skill Name</fullName>
      <default>false</default>
      <label>New Skill Name</label>
  </customValue>
  ```

  **2. Add to the Personal_Experience RecordType** in alphabetical order within `<picklistValues>`:
  `force-app/main/default/objects/Experience__c/recordTypes/Personal_Experience.recordType-meta.xml`
  ```xml
  <values>
      <fullName>New Skill Name</fullName>
      <default>false</default>
  </values>
  ```

  **3. Deploy both files together:**
  ```bash
  cd ~/Salesforce_Projects/LWRPortfolioWebsite
  sf project deploy start \
    --metadata "GlobalValueSet:Skills" \
    --metadata "RecordType:Experience__c.Personal_Experience" \
    --target-org your-sf-user@example.com
  ```
  Confirm the deploy succeeded before proceeding. Deploying the GlobalValueSet alone is not sufficient — the new value will exist in the value set but Salesforce will still reject it on write-back with `INVALID_OR_NULL_FOR_RESTRICTED_PICKLIST`.

- If no: Remove it from the skills list.

---

## Step 3: Generate Draft

Generate the Experience record draft. Present it in this format:

```
DRAFT EXPERIENCE RECORD
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Name:        {project name}
Start Date:  {YYYY-MM-DD}
End Date:    {YYYY-MM-DD or "Ongoing"}
GitHub URL:  {URL or "—"}
Skills:      {comma-separated list}

Description (HTML):
{description in <p> tags}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Description writing guidance:**
- Write 2–4 sentences summarizing what the project is, what was built, and key technical decisions
- Use `<p>` tags for paragraphs
- Past tense if the project is complete; present tense if ongoing
- Reference specific technologies and outcomes (not just "I built a thing")

---

## Step 4: User Review

Present the draft and ask:

```
Review the Experience record above.

Actions:
- Approve as-is: type "approve"
- Edit a field: type "edit [field]: [new value]"  (e.g., "edit skills: Python, Docker, SQL")
- Cancel: type "cancel"
```

Process edits if requested. Re-present the draft after each edit. Repeat until the user types "approve" or "cancel". Do not proceed past this step without explicit approval.

If the user cancels: Stop. Do not call any scripts.

---

## Step 5: Stage to PostgreSQL

After approval, build the JSON payload from the approved draft:

```json
{
  "name": "...",
  "start_date": "YYYY-MM-DD",
  "end_date": "YYYY-MM-DD or null",
  "description": "<p>...</p>",
  "skills": ["Skill1", "Skill2"],
  "github_url": "https://... or null"
}
```

Run the staging script:

```bash
cd ~/Docker_Projects/Salesforce_Backup
PYTHONPATH=. POSTGRES_HOST=localhost POSTGRES_SSLMODE=disable \
    python3 -m src.document_project --json '<payload_json_here>'
```

**If the script exits with error:**
- Skill validation error (contains "Invalid skill(s)"): The skill name mismatches the SF value set. Check the exact casing in the XML, correct the skill in the payload, and retry. Do not retry more than once without showing the user the corrected skill name.
- Connection error: Report the error message and stop. Do not retry automatically. Suggest the user verify `docker ps` and `.env` credentials.

Confirm the output contains `Staged experience '{name}'` before proceeding to Step 6.

---

## Step 6: Push to Salesforce

Run the write-back worker:

```bash
cd ~/Docker_Projects/Salesforce_Backup
PYTHONPATH=. POSTGRES_HOST=localhost POSTGRES_SSLMODE=disable \
    python3 -m src.write_back
```

**If write-back succeeds:** Report the Salesforce ID from the output.

**If write-back fails:**
Report the error. `write_back.py` sets `sync_status='failed'` on the record — subsequent runs will skip it entirely (it queries `WHERE sync_status = 'pending_push'`). To retry after fixing the root cause, reset the record:

```bash
cd ~/Docker_Projects/Salesforce_Backup
PYTHONPATH=. POSTGRES_HOST=localhost POSTGRES_SSLMODE=disable python3 -c "
from src.database import connect
conn = connect()
cur = conn.cursor()
cur.execute(\"UPDATE experience SET sync_status='pending_push', error_message=NULL WHERE name='<record_name>'\")
conn.commit()
conn.close()
print('Reset to pending_push.')
"
```

Then re-run write-back:
```bash
PYTHONPATH=. POSTGRES_HOST=localhost POSTGRES_SSLMODE=disable python3 -m src.write_back
```

**Important:** The correct import is `from src.database import connect`. Do not use `from src.db import get_connection` — that module does not exist.

---

## Step 7: Confirm

Report the result:

```
Experience '{name}' documented and published to Salesforce.
  Salesforce ID: {id_from_write_back_output}
  Skills:        {skills}
  Parent Job:    Awesome Opossum (a02al000000d197AAA)

To verify: sf data query \
  --query "SELECT Id, Name, Skills__c, GitHub_URL__c FROM Experience__c WHERE Id = '{id}'" \
  --target-org your-sf-user@example.com
```
