import json

def merge_map_triggers(path):
    with open(path, 'r') as f:
        data = json.load(f)
    for quest in data:
        triggers = quest.get('event_triggers', [])
        new_trigs = []
        skip_next = False
        for i, trig in enumerate(triggers):
            if skip_next:
                skip_next = False
                continue
            if trig.get('type') == 'current_map_id_is' and i + 1 < len(triggers) and triggers[i+1].get('type') == 'previous_map_id_was':
                curr = trig.get('map_id')
                prev = triggers[i+1].get('map_id')
                new_trigs.append({
                    'type': 'current_map_is_previous_map_was',
                    'current_map_id': curr,
                    'previous_map_id': prev,
                    'comment': f"{prev} -> {curr}",
                    'completed': trig.get('completed', False)
                })
                skip_next = True
            else:
                new_trigs.append(trig)
        quest['event_triggers'] = new_trigs
    with open(path, 'w') as f:
        json.dump(data, f, indent=4)

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Merge map triggers in JSON file')
    parser.add_argument('json_file', help='Path to required_completions.json')
    args = parser.parse_args()
    merge_map_triggers(args.json_file) 