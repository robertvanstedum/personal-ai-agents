# German Domain Orchestrator Instructions
**Version:** 1.0
**Read this file before handling any German domain command.**
**These instructions are binding and override agent memory.**

---

## Path Variables

MINI_MOI_ROOT = ~/Projects/personal-ai-agents
GERMAN_ROOT   = _NewDomains/language-german/
GERMAN_BASE   = _NewDomains/language-german/language/german/
ANKI_IMPORT   = _NewDomains/language-german/language/german/utilities/anki_import/

---

## Command Routing

For every German domain command:
1. Read the command mapping below
2. Run the exact shell command using your shell tool
3. Forward stdout verbatim — do not paraphrase, summarize, or modify
4. If the command fails: forward stderr verbatim with a ⚠️ prefix
5. Never generate content yourself

---

## Commands

### Session Pull
Triggers: !german session, pull today's German session, German session
please, What's my German session today?, give me today's prompt,
next German session

Shell command:
```
cd $MINI_MOI_ROOT && source venv/bin/activate && \
python $GERMAN_ROOT/get_german_session.py \
  --base-dir $GERMAN_BASE --dropbox --send
```

### Writing Session
Triggers: !german writing, writing session, next writing session,
writing [scenario] [persona]

Shell command: same as session pull
Append to output: "⌨️ WRITING SESSION — Add Mode: writing to transcript header."

### Drill Mode
Triggers: !german drill [N], drill [N], drill [scenario] [persona] [N]

Shell command:
```
cd $MINI_MOI_ROOT && source venv/bin/activate && \
python $GERMAN_ROOT/get_german_session.py \
  --base-dir $GERMAN_BASE --dropbox --send --drill [N]
```

### Status
Triggers: !german status, German status, how am I doing

Shell command:
```
cd $MINI_MOI_ROOT && source venv/bin/activate && \
python $GERMAN_ROOT/status.py --base-dir $GERMAN_BASE
```

### Anki Import
Triggers: !german anki, import anki cards, upload anki

Shell command:
```
cd $MINI_MOI_ROOT && source venv/bin/activate && \
python $GERMAN_ROOT/import_cards.py
```

### Watcher Start
Triggers: !german watcher start, start the watcher

Shell command:
```
cd $MINI_MOI_ROOT/$GERMAN_ROOT && \
$MINI_MOI_ROOT/venv/bin/python3 watch_transcripts.py &
```

### Watcher Stop
Triggers: !german watcher stop, stop the watcher

Shell command:
```
pkill -f watch_transcripts.py
```

---

## Rules

1. Never generate session content yourself — the scripts do it
2. Never summarize or paraphrase stdout — forward verbatim
3. Never route !german session to @minimoi_cmd_bot — wrong bot
4. Always read current lesson from files — never from memory
5. Never hardcode absolute paths — use the path variables above

---

## Machine Migration

Update the four path variables at the top when changing machines.
All commands follow automatically.
