import os
import re

class CriticalPathPlanner:
    def __init__(self):
        # Locate the critical path graph relative to this file
        graph_file = os.path.abspath(
            os.path.join(os.path.dirname(__file__), '..', 'game_data', 'critical_path_directed_graph.txt')
        )
        self.steps = []
        self.mapping = {}
        self._parse_graph(graph_file)

    def _normalize(self, s: str) -> str:
        return s.strip().lower() if s else ''

    def _parse_graph(self, graph_file: str):
        prev_loc = None
        prev_loc_text = None
        try:
            with open(graph_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        except FileNotFoundError:
            return
        for raw in lines:
            # Skip empty lines and comments
            stripped = raw.strip()
            if not stripped or stripped.startswith('#'):
                continue
            # Action lines are indented and start with the arrow
            if raw.startswith(' ') or raw.startswith('\t'):
                if '↓' in raw and prev_loc:
                    m = re.search(r'↓.*\((.*?)\)', raw)
                    if m:
                        action = m.group(1).strip()
                        # Append step with full location text
                        self.steps.append({'loc': prev_loc, 'action': action, 'loc_text': prev_loc_text})
                        prev_loc = None
            else:
                # Detect inline travel instructions in location lines
                m_inline = re.search(r'^(.*?)\((Travel[^)]*)\)', stripped)
                if m_inline:
                    loc_name = m_inline.group(1).strip()
                    action_inline = m_inline.group(2).strip()
                    self.steps.append({'loc': loc_name, 'action': action_inline, 'loc_text': stripped})
                    # Preserve prev_loc for arrow (next indented) detection
                    prev_loc = loc_name
                    prev_loc_text = stripped
                    continue
                # Location lines (non-indented, not note blocks)
                if stripped.startswith('['):
                    continue
                # Capture full location with description
                loc_text = stripped
                loc_name = loc_text.split('(')[0].strip()
                prev_loc = loc_name
                prev_loc_text = loc_text
        # Build mapping from each location to its primary and secondary actions
        for i, entry in enumerate(self.steps):
            loc_key = self._normalize(entry['loc'])
            # Only set mapping once per location
            if loc_key in self.mapping:
                continue
            primary_action = entry['action']
            loc_text = entry.get('loc_text', entry['loc'])
            # Determine secondary action if next step is the same location
            secondary_action = None
            if i + 1 < len(self.steps) and self._normalize(self.steps[i+1]['loc']) == loc_key:
                secondary_action = self.steps[i+1]['action']
            self.mapping[loc_key] = (loc_text, primary_action, secondary_action)

    def get_next_step(self, current_location: str):
        key = self._normalize(current_location)
        # Get full mapping tuple: (loc_text, action, next_zone)
        triple = self.mapping.get(key)
        if triple:
            return triple
        # Fallback: no description available, return input location, default action 'explore', no zone
        loc_text = current_location
        return (loc_text, 'explore', None)

# Global planner instance
planner = CriticalPathPlanner() 