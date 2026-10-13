# The brain's skill eval (plugins/marcel/skills/evals/brain-routing.json)

Two ways to run the same cases:

- **Scripted, in the gate** (`make -C plugins/marcel/tools check`): `tests/fake_brain.py` replays the turn
  recorded for each case (`tests/brain_replays.json`) through the real tools server to the hub in-process.
  It proves the skill still says the rule the turn relies on and that the calls work against the hub. It
  does not prove a real model makes those calls.
- **Live, on the NUC** (`make -C plugins/marcel/tools test-live`): `claude -p` on Opus with the
  `orchestrate` skill against canned answers. This is the one that shows what the brain really does.

## Results

- 2026-10-14, scripted (S-21.1): the eight `delegate-*` cases (ADR-009) pass: two answers
  (`delegate-answer-fact`, `delegate-answer-thread`), three NUC quests (`delegate-nuc-docker`,
  `delegate-nuc-photos`, `delegate-nuc-approve`), two cloud quests (`delegate-cloud-typo`,
  `delegate-cloud-bump`) and one schedule (`delegate-schedule`). The brain has no Agent or Workflow tool,
  and its settings deny both (`brain/tests/test_settings.py`).
- Live run: not yet. Record it here, one line per case that went another way than its `expect`, with the
  calls the model made (from `tests/.eval/`).
