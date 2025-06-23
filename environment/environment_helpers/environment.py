# FORCE QUEST LOADING IF NEEDED
# Always load the current quest path when coordinates are empty or mismatched
if current_quest and (not self.navigator.sequential_coordinates or self.navigator.active_quest_id != current_quest):
    print(f"🎯 FORCE LOADING quest {current_quest} into navigator")
    success = self.navigator.load_coordinate_path(current_quest)
    print(f"🎯 Force load result: {success}") 