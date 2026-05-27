# notifai — Task List

## Phase 1: Foundation
- [x] **Task 1** — `requirements.txt` + `queries.yaml` (XS)

### Checkpoint 1
- [x] `pip install -r requirements.txt` succeeds
- [x] `queries.yaml` parses cleanly

## Phase 2: Core Runner
- [x] **Task 2** — `check.py` full daily runner — Claude API + Resend (M)

### Checkpoint 2 — Steel Thread
- [x] `python check.py` runs end-to-end locally
- [x] Email received for a YES condition
- [x] Human review ✋

## Phase 3: CI Automation
- [x] **Task 3** — `.github/workflows/notify.yml` (XS)

### Checkpoint 3 — Always-On
- [x] Manual `workflow_dispatch` succeeds in GHA
- [x] Human review ✋

## Phase 4: Query Authoring
- [x] **Task 4** — `assist.py` interactive query builder (S)

### Checkpoint 4 — Complete
- [x] All five files functional
- [x] End-to-end verified
