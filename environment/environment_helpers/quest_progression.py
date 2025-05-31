from typing import List, Dict
import json
from pathlib import Path

class QuestProgressionEngine:
    def __init__(self, env, navigator, quest_manager, quests: List[Dict], quest_ids_all: List[int], status_queue, run_dir: Path):
        self.env = env
        self.navigator = navigator
        self.quest_manager = quest_manager
        self.QUESTS = quests
        self.quest_ids_all = quest_ids_all
        self.status_queue = status_queue
        self.run_dir = run_dir
        # Track trigger and quest completion
        self.trigger_completed: Dict[str, bool] = {}
        self.quest_completed: Dict[int, bool] = {qid: False for qid in quest_ids_all}

    def step(self, evaluator):
        # 1) evaluate triggers for current quest
        current_qid = getattr(self.quest_manager, 'current_quest_id', None)
        if current_qid is not None:
            active = next((qq for qq in self.QUESTS if int(qq['quest_id']) == current_qid), None)
            if active:
                # process event_triggers
                for idx, trg in enumerate(active.get('event_triggers', [])):
                    tid = f"{active['quest_id']}_{idx}"
                    # only check if previous triggers complete
                    if idx == 0 or all(self.trigger_completed.get(f"{active['quest_id']}_{i}", False) for i in range(idx)):
                        if not self.trigger_completed.get(tid, False) and evaluator.check_trigger(trg):
                            self.trigger_completed[tid] = True
                            self.status_queue.put((tid, True))
                    else:
                        break
                # mark quest complete if all triggers done
                tids = [f"{active['quest_id']}_{i}" for i in range(len(active.get('event_triggers', [])))]
                if tids and all(self.trigger_completed.get(t, False) for t in tids):
                    qint = int(active['quest_id'])
                    if not self.quest_completed[qint]:
                        self.quest_completed[qint] = True
                        self.status_queue.put((active['quest_id'], True))
                    active['completed'] = True
                # auto-complete subquests of form "Complete quest X."
                for sidx, step_text in enumerate(active.get('subquest_list', [])):
                    if step_text.startswith("Complete quest "):
                        parts = step_text.split()
                        if len(parts) >= 3:
                            prev_q = int(parts[2].rstrip('.'))
                            if self.quest_completed.get(prev_q, False):
                                step_id = f"{active['quest_id']}_step_{sidx}"
                                self.status_queue.put((step_id, True))
        # 2) advance to next quest if needed
        current_qid = getattr(self.quest_manager, 'current_quest_id', None)
        if current_qid is not None and self.quest_completed.get(current_qid, False):
            # find next
            for qid in self.quest_ids_all:
                if not self.quest_completed[qid]:
                    next_qid = qid
                    break
            else:
                next_qid = None
            if next_qid and next_qid != current_qid:
                self.quest_manager.current_quest_id = next_qid
                self.navigator.active_quest_id = next_qid
                self.env.current_loaded_quest_id = next_qid
                self.status_queue.put(('__current_quest__', next_qid))
        # 3) persist statuses
        try:
            with open(self.run_dir / 'trigger_status.json', 'w') as f:
                json.dump(self.trigger_completed, f, indent=4)
            with open(self.run_dir / 'quest_status.json', 'w') as f:
                json.dump({str(qid).zfill(3): val for qid, val in self.quest_completed.items()}, f, indent=4)
        except Exception as e:
            print(f"QuestProgressionEngine: error writing status files: {e}") 