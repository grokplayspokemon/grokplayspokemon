import json
import os
from typing import Dict, List, Optional


class QuestManager:
    """
    QuestManager loads quest definitions from a JSON file and provides methods
    to access and display quest information for the current active quest.
    """

    def __init__(self, quests_path: Optional[str] = None):
        if quests_path is None:
            # Default to the required_completions.json in the same directory
            quests_path = os.path.join(os.path.dirname(__file__), 'required_completions.json')
        with open(quests_path, 'r') as f:
            self.quests: List[Dict] = json.load(f)

    def get_quest(self, quest_id: str) -> Dict:
        """
        Retrieve a quest dict by its quest_id.
        """
        for quest in self.quests:
            if quest.get('quest_id') == quest_id:
                return quest
        raise KeyError(f"Quest with id '{quest_id}' not found")

    def get_first_step(self, quest: Dict) -> Optional[Dict]:
        """
        Return the first step of a quest, or None if no steps defined.
        """
        steps = quest.get('steps', [])
        return steps[0] if steps else None

    def get_all_steps(self, quest: Dict) -> List[Dict]:
        """
        Return the full list of steps for a quest.
        """
        return quest.get('steps', [])

    def display_quest_info(self, quest_id: str) -> None:
        """
        Print out the quest title, start location, first step, and full list of steps.
        """
        quest = self.get_quest(quest_id)
        title = quest.get('title', '<Unnamed Quest>')
        print(f"Current Quest: {title} (ID: {quest_id})")

        start_loc = quest.get('start_location')
        if start_loc:
            name = start_loc.get('name')
            map_id = start_loc.get('map_id')
            print(f"Start Location: {name} (Map ID: {map_id})")

        first_step = self.get_first_step(quest)
        if first_step:
            print(f"First Step: {first_step.get('description', '<No description>')}")
        else:
            print("First Step: <None defined>")

        print("All Steps:")
        for idx, step in enumerate(self.get_all_steps(quest), start=1):
            desc = step.get('description', '')
            print(f"  {idx}. {desc}")


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Display quest information for streaming/prototyping.')
    parser.add_argument('quest_id', help='The quest_id to display')
    parser.add_argument('--path', help='Path to required_completions.json', default=None)
    args = parser.parse_args()

    qm = QuestManager(args.path)
    qm.display_quest_info(args.quest_id) 