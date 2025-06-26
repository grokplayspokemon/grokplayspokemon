import sys
from typing import Union
import uuid 
import os
from math import floor, sqrt
import json
import pickle
from pathlib import Path

import copy
import random
import numpy as np
from einops import rearrange

from environment.data.environment_data.ram_map import read_m, write_mem
from environment.data.environment_data.constants import ALL_GOOD_ITEMS, GOOD_ITEMS_PRIORITY, \
    POKEBALL_PRIORITY, POTION_PRIORITY, REVIVE_PRIORITY
from environment.data.environment_data.constants import MART_ITEMS_ID_DICT, ITEM_TM_IDS_PRICES
from environment.data.environment_data.ram_addresses import RamAddress as RAM
from environment.data.environment_data.items import Items
from environment.data.environment_data.bag import Bag

from environment.environment_helpers.navigator import InteractiveNavigator
from environment.data.recorder_data.global_map import local_to_global, global_to_local
from functools import partial

ITEM_ID_TO_NAME_DICT = {
    0x01: "MASTER_BALL",
    0x02: "ULTRA_BALL",
    0x03: "GREAT_BALL",
    0x04: "POKE_BALL",
    0x05: "TOWN_MAP",
    0x06: "BICYCLE",
    0x07: "CELADON_DEPT_STORE_KEY",
    0x08: "SAFARI_BALL",
    0x09: "POKEDEX",
    0x0A: "MOON_STONE",
    0x0B: "ANTIDOTE",
    0x0C: "BURN_HEAL",
    0x0D: "ICE_HEAL",
    0x0E: "AWAKENING",
    0x0F: "PARALYZE_HEAL",
    0x10: "FULL_RESTORE",
    0x11: "MAX_POTION",
    0x12: "HYPER_POTION",
    0x13: "SUPER_POTION",
    0x14: "POTION",
    0x15: "BOOSTER_PACK",
    0x16: "ESCAPE_ROPE",
    0x17: "REPEL",
    0x18: "OLD_AMBER",
    0x19: "FIRE_STONE",
    0x1A: "THUNDER_STONE",
    0x1B: "WATER_STONE",
    0x1C: "HP_UP",
    0x1D: "PROTEIN",
    0x1E: "IRON",
    0x1F: "CARBOS",
    0x20: "CALCIUM",
    0x21: "RARE_CANDY",
    0x22: "DOME_FOSSIL",
    0x23: "HELIX_FOSSIL",
    0x24: "SECRET_KEY",
    0x25: "BIKE_VOUCHER",
    0x26: "X_ACCURACY",
    0x27: "LEAF_STONE",
    0x28: "CARD_KEY",
    0x29: "NUGGET",
    0x2A: "POKEDOLL",
    0x2B: "FULL_HEAL",
    0x2C: "REVIVE",
    0x2D: "MAX_REVIVE",
    0x2E: "GUARD_SPEC",
    0x2F: "SUPER_REPEL",
    0x30: "MAX_REPEL",
    0x31: "DIRE_HIT",
    0x32: "COIN_CASE",
    0x33: "ITEMFINDER",
    0x34: "EXP_ALL",
    0x35: "OLD_ROD",
    0x36: "GOOD_ROD",
    0x37: "SUPER_ROD",
    0x38: "PP_UP",
    0x39: "ETHER",
    0x3A: "MAX_ETHER",
    0x3B: "ELIXIR",
    0x3C: "MAX_ELIXIR",
    0x3D: "DIG_TM", # TM01
    0x3E: "TOXIC_TM", # TM06
    0x3F: "HORN_ATTACK_TM", # TM02
    0x40: "PAY_DAY_TM", # TM03
    0x41: "SUBMISSION_TM", # TM04
    0x42: "MEGA_PUNCH_TM", # TM05
    0x43: "SEISMIC_TOSS_TM", # TM07
    0x44: "DRAGON_RAGE_TM", # TM08
    0x45: "MEGA_DRAIN_TM", # TM09
    0x46: "OAKS_PARCEL",
    0x47: "SURF_HM", # HM03
    0x48: "FLY_HM", # HM02
    0x49: "STRENGTH_HM", # HM04
    0x4A: "FLASH_HM", # HM05
    0x4B: "CUT_HM", # HM01
    0x4C: "BRAWL_TM", # TM00
}

class ItemHandler:
    def __init__(self, env):
        self.env = env
        self.pyboy = env.pyboy
        self.nav = InteractiveNavigator(env)
        self.read_m = partial(read_m, self.pyboy)
        self._items_in_bag = None
        self._last_item_count = 0
        self.use_mart_count = 0
        self.battle_type = 0
        self.init_caches()
        self.bag = Bag(self.pyboy)
        
    def init_caches(self):
        # for cached properties
        self._all_events_string = ''
        self._battle_type = None
        self._cur_seen_map = None
        self._minimap_warp_obs = None
        self._is_warping = None
        self._minimap_obs = None
        self._minimap_sprite = None
        self._bottom_left_screen_tiles = None
        self._num_mon_in_box = None
    
    def get_badges(self):
        return self.read_m("wObtainedBadges").bit_count()
    
    def is_in_battle(self):
        # D057
        # 0 not in battle
        # 1 wild battle
        # 2 trainer battle
        # -1 lost battle
        return self.battle_type > 0
    
    def get_items_in_bag(self):
        """Reads items and their quantities directly from RAM into a dictionary."""
        if self._items_in_bag is None:
            items_dict = {}
            current_addr = 0xD31E  # Start of item IDs in RAM
            # Loop through possible item slots (max 20 items, each taking 2 bytes: ID, Quantity)
            for _ in range(20):
                item_id = self.read_m(current_addr)
                if item_id == 0 or item_id == 0xFF:  # 0 or 0xFF usually indicates end of list
                    break
                
                quantity = self.read_m(current_addr + 1) # Quantity is right after item ID
                items_dict[item_id] = quantity
                current_addr += 2  # Move to the next item ID
            
            self._items_in_bag = items_dict
            print(f"[ITEM_HANDLER DEBUG] get_items_in_bag: Found items dict: {self._items_in_bag}")
        else:
            items_dict = self._items_in_bag
        return items_dict
    
    def get_items_quantity_in_bag(self):
        """Returns a list of quantities of all items currently in the bag."""
        return list(self.get_items_in_bag().values())
    
    def get_item_quantity(self, item_id):
        """Returns the quantity of a specific item by its ID from the bag."""
        items_in_bag = self.get_items_in_bag()
        quantity = items_in_bag.get(item_id, 0)
        item_name = ITEM_ID_TO_NAME_DICT.get(item_id, f"UNKNOWN_ITEM_{hex(item_id)}")
        print(f"[ITEM_HANDLER DEBUG] get_item_quantity: Querying for {item_name} (ID: {hex(item_id)}), found quantity: {quantity}")
        return quantity
    
    def read_bit(self, address, bit_index):
        """Read a specific bit from a memory address"""
        byte_value = self.read_m(address)
        return bool(byte_value & (1 << bit_index))

    def force_add_oaks_parcel(self):
        """Forcefully add Oak's Parcel to the bag when the acquisition dialog is detected"""
        oaks_parcel_id = 0x46
        
        try:
            from environment.environment_helpers.stage_helper import parcel_logger
            parcel_logger.critical(f"[ITEM_HANDLER] FORCE ADDING OAK'S PARCEL!")
        except ImportError:
            print(f"[ITEM_HANDLER] FORCE ADDING OAK'S PARCEL!")
        
        # Use the existing buy_item method to add the parcel
        self.buy_item(oaks_parcel_id, 1, 0)  # item_id, quantity, price (free)
        
        try:
            from environment.environment_helpers.stage_helper import parcel_logger
            parcel_logger.critical(f"[ITEM_HANDLER] FORCED OAK'S PARCEL ADDITION COMPLETE!")
        except ImportError:
            print(f"[ITEM_HANDLER] FORCED OAK'S PARCEL ADDITION COMPLETE!")

    def force_add_town_map(self):
        """Forcefully add Town Map to the bag when the acquisition dialog is detected"""
        town_map_id = 0x5
        
        print(f"[ITEM_HANDLER DEBUG] force_add_town_map: Attempting to force add TOWN_MAP (ID: {hex(town_map_id)})")
        
        try:
            from environment.environment_helpers.stage_helper import parcel_logger
            parcel_logger.critical(f"[ITEM_HANDLER] FORCE ADDING TOWN MAP!")
        except ImportError:
            print(f"[ITEM_HANDLER] FORCE ADDING TOWN MAP!")
        
        # Use the existing buy_item method to add the town map
        result = self.buy_item(town_map_id, 1, 0)  # item_id, quantity, price (free)
        print(f"[ITEM_HANDLER DEBUG] force_add_town_map: buy_item returned: {result}")
        
        try:
            from environment.environment_helpers.stage_helper import parcel_logger
            parcel_logger.critical(f"[ITEM_HANDLER] FORCED TOWN MAP ADDITION COMPLETE!")
        except ImportError:
            print(f"[ITEM_HANDLER] FORCED TOWN MAP ADDITION COMPLETE!")

        # Invalidate the cache to force a re-read of the bag contents
        self._items_in_bag = None

    def force_refresh_item_cache(self):
        """Forces a refresh of the item cache by invalidating it."""
        print("[ITEM_HANDLER DEBUG] force_refresh_item_cache: Forcing item bag cache invalidation.")
        self._items_in_bag = None

    def get_items_in_bag_list(self):
        """Returns a list of item IDs in bag preserving order."""
        return list(self.get_items_in_bag().keys())

    def scripted_manage_items(self):
        # Use dict for quantities and list for order-based operations
        items_dict = self.get_items_in_bag()
        items = list(items_dict.keys())

        # HACK: Ensure Oak\'s Parcel is in the bag during quest 12
        current_quest_id = None
        if hasattr(self.env, 'quest_manager') and self.env.quest_manager:
            current_quest_id = getattr(self.env.quest_manager, 'current_quest_id', None)
        if current_quest_id == 12:
            print(f'[ITEM_HANDLER] SCRIPTED MANAGE ITEMS: CURRENT QUEST ID: {current_quest_id}')
            oaks_parcel_id = 0x46
            print(f'[ITEM_HANDLER] SCRIPTED MANAGE ITEMS: OAK\'S PARCEL ID: {oaks_parcel_id}')
            print(f'[ITEM_HANDLER] SCRIPTED MANAGE ITEMS: ITEMS: {items}')
            if oaks_parcel_id not in items:
                print(f'[ITEM_HANDLER] SCRIPTED MANAGE ITEMS: OAK\'S PARCEL NOT IN BAG, FORCING ADDITION')
                self.force_add_oaks_parcel()
                # Also set the Oak\'s Parcel event so quest progression sees it
                self.env.events.set_event("EVENT_GOT_OAKS_PARCEL", True)
                print("[ITEM_HANDLER] EVENT_GOT_OAKS_PARCEL flag set!")
                # Refresh items list after forcing addition
                self.force_refresh_item_cache()
                items_dict = self.get_items_in_bag()
                items = list(items_dict.keys())
                print(f'[ITEM_HANDLER] FORCED OAK\'S PARCEL ADDITION COMPLETE! {items}')

            # FORCE ADD PARCEL: Check if "Got Oaks Parcel" event flag is set but parcel not in bag
            # DO THIS BEFORE EARLY RETURNS so it always runs when the flag is set
            try:
                # Check the "Got Oaks Parcel" event flag at memory address 0xD74E bit 1
                got_oaks_parcel_flag = self.read_bit(0xD74E, 1)
                oaks_parcel_id = 0x46

                if got_oaks_parcel_flag and oaks_parcel_id not in items:
                    self.force_add_oaks_parcel()

                    # Refresh items list after forcing addition
                    self.force_refresh_item_cache()
                    items_dict = self.get_items_in_bag()
                    items = list(items_dict.keys())
            except Exception as e:
                print(f"[ITEM_HANDLER] Error in force parcel check: {e}")

        # FORCE ADD TOWN MAP: Check if "Got Town Map" event flag is set but town map not in bag
        # Same issue as Oak\'s Parcel - NPC gives item but it never gets added to bag
        # ONLY for quests before 23 to allow normal gameplay for later quests
        try:
            # Get current quest ID to limit force-add to early quests only
            current_quest_id = None
            if hasattr(self, 'env') and hasattr(self.env, 'quest_manager') and self.env.quest_manager:
                current_quest_id = getattr(self.env.quest_manager, 'current_quest_id', None)

            # Only force-add Town Map for quests before 23
            if current_quest_id is None or current_quest_id < 23:
                # Check the "Got Town Map" event flag at memory address 0xD74A bit 0
                got_town_map_flag = self.read_bit(0xD74A, 0)
                town_map_id = 0x5  # Town Map item ID
                current_map = self.read_m("wCurMap")

                print(f"[ITEM_HANDLER DEBUG] scripted_manage_items: Town Map Check - current_map: {current_map}, got_town_map_flag: {got_town_map_flag}, town_map_id (0x5) not in items: {town_map_id not in items}")

                if got_town_map_flag and town_map_id not in items:  # Only check flag and if not in bag
                    print(f"[ITEM_HANDLER DEBUG] scripted_manage_items: Condition met to force_add_town_map - got_town_map_flag={got_town_map_flag}, town_map_id not in items={town_map_id not in items}!")
                    print("[ITEM_HANDLER DEBUG] scripted_manage_items: Triggering force_add_town_map!")
                    self.force_add_town_map()

                    # Refresh items list after forcing addition
                    self.force_refresh_item_cache()
                    items_dict = self.get_items_in_bag()
                    items = list(items_dict.keys()) # Re-fetch items after cache invalidation
                    print("[ITEM_HANDLER] FORCE ADD TOWN MAP: TOWN MAP ADDITION COMPLETE AND CACHE RESET!")
        except Exception as e:
            print(f"[ITEM_HANDLER] Error in force town map check: {e}")

        # NOW check early return conditions after force-add logic
        if self._last_item_count == len(items):
            return

        if self.read_m("wIsInBattle") > 0 or self.read_m(0xFFB0) == 0:  # hWY in menu
            return

        # Identify Oak\'s Parcel ID for tracking
        oaks_parcel_id = 0x46

        # pokeballs = [0x01, 0x02, 0x03, 0x04]

        if len(items) == 20:
            # bag full, delete 1 item
            # do not delete pokeballs and ALL_KEY_ITEMS
            # try to delete the last item first
            # if it is not the last item, swap with the last item
            # set the address after the last item to 255
            # set the address after the last quantity to 0
            tmp_item = items[-1]
            tmp_item_quantity = self.get_item_quantity(tmp_item)

            deleted = False
            for i in range(19, -1, -1):
                if items[i] not in ALL_GOOD_ITEMS:
                    if i == 19:
                        # delete the last item
                        write_mem(self.pyboy, 0xD31E + i*2, 0xff)
                        write_mem(self.pyboy, 0xD31F + i*2, 0)
                    else:
                        # swap with last item
                        write_mem(self.pyboy, 0xD31E + i*2, tmp_item)
                        write_mem(self.pyboy, 0xD31F + i*2, tmp_item_quantity)
                        # set last item to 255
                        write_mem(self.pyboy, 0xD31E + 19*2, 0xff)
                        write_mem(self.pyboy, 0xD31F + 19*2, 0)
                    deleted = True
                    break

            if not deleted:
                # delete good items if no other items
                # from first to last good items priority
                for good_item in GOOD_ITEMS_PRIORITY:
                    if good_item in items:
                        idx = items.index(good_item)
                        if good_item == oaks_parcel_id:
                            print(f"[ITEM_HANDLER] CRITICAL ERROR: TRYING TO DELETE OAK\'S PARCEL!")
                        if idx == 19:
                            # delete the last item
                            write_mem(self.pyboy, 0xD31E + idx*2, 0xff)
                            write_mem(self.pyboy, 0xD31F + idx*2, 0)
                        else:
                            # swap with last item
                            write_mem(self.pyboy, 0xD31E + idx*2, tmp_item)
                            write_mem(self.pyboy, 0xD31F + idx*2, tmp_item_quantity)
                            # set last item to 255
                            write_mem(self.pyboy, 0xD31E + 19*2, 0xff)
                            write_mem(self.pyboy, 0xD31F + 19*2, 0)
                        deleted = True
                        break

            # reset cache and get items again
            self.force_refresh_item_cache()
            items_dict = self.get_items_in_bag()
            items = list(items_dict.keys())
            # write_mem(self.pyboy, 0xD31D, len(items)) ## replaced with bag class

        item_idx_ptr = 0
        # sort good items to the front based on priority
        for good_item in GOOD_ITEMS_PRIORITY:
            if good_item in items:
                all_items_quantity = self.get_item_quantity(good_item)
                idx = items.index(good_item)
                if idx == item_idx_ptr:
                    # already in the correct position
                    item_idx_ptr += 1
                    continue
                cur_item_quantity = all_items_quantity
                tmp_item = items[item_idx_ptr]
                tmp_item_quantity = self.get_item_quantity(tmp_item)
                # print(f'Swapping item {item_idx_ptr}:{tmp_item}/{tmp_item_quantity} with {idx}:{good_item}/{cur_item_quantity}')
                # swap
                write_mem(self.pyboy, 0xD31E + item_idx_ptr*2, good_item)
                write_mem(self.pyboy, 0xD31F + item_idx_ptr*2, cur_item_quantity)
                write_mem(self.pyboy, 0xD31E + idx*2, tmp_item)
                write_mem(self.pyboy, 0xD31F + idx*2, tmp_item_quantity)
                item_idx_ptr += 1
                # reset cache and get items again
                self.force_refresh_item_cache()
                items_dict = self.get_items_in_bag()
                items = list(items_dict.keys())
                # print(f'Moved good item: {good_item} to pos: {item_idx_ptr}')
        self._last_item_count = len(self.get_items_in_bag())

        # Call scripted_buy_items
        self.scripted_buy_items()

    def scripted_buy_items(self):
        if self.read_m(0xFFB0) == 0:  # hWY in menu
            print(f"ERR item_handler.py: scripted_buy_items(): hWY in menu")
            return False
        # check mart items
        # if mart has items in GOOD_ITEMS_PRIORITY list (the last item is the highest priority)
        #  check if have enough (10) of the item
        #   if not enough, check if have enough money to buy the item
        #    if have enough money, check if bag is 19/20
        #     if bag is 19/20, sell 1 item
        #      handle if all items are key or good items
        #     buy the item by deducting money and adding item to bag

        # will try to buy 10 best pokeballs offered by mart
        # and 10 best potions and revives offered by mart
        mart_items = self.get_mart_items()
        print(f"item_handler.py: scripted_buy_items(): mart_items: {mart_items}")
        if not mart_items:
            print(f"ERR item_handler.py: scripted_buy_items(): not in mart or incorrect x, y")
            # not in mart or incorrect x, y
            # or mart_items is empty for purchasable items
            return False
        bag_items_dict = self.get_items_in_bag()
        bag_items = list(bag_items_dict.keys())  # ordered list of item IDs
        item_list_to_buy = [POKEBALL_PRIORITY, POTION_PRIORITY, REVIVE_PRIORITY]
        target_quantity = 10
        print(f"item_handler.py: scripted_buy_items(): bag_items: {bag_items}, item_list_to_buy: {item_list_to_buy}")
        for n_list, item_list in enumerate(item_list_to_buy):
            if self.get_badges() >= 7:
                if n_list == 0:
                    # pokeball
                    target_quantity = 5
                elif n_list == 1:
                    # potion
                    target_quantity = 20
                elif n_list == 2:
                    # revive
                    target_quantity = 10
            best_in_mart_id, best_in_mart_priority = self.get_best_item_from_list(item_list, mart_items)
            best_in_bag_id, best_in_bag_priority = self.get_best_item_from_list(item_list, bag_items)
            bag_items_for_index = list(bag_items_dict.keys()) # Ensure list for index()
            best_in_bag_idx = bag_items_for_index.index(best_in_bag_id) if best_in_bag_id is not None else None
            best_in_bag_quantity = self.get_item_quantity(best_in_bag_id) if best_in_bag_idx is not None else None
            print(f"item_handler.py: scripted_buy_items(): best_in_mart_id: {best_in_mart_id}, best_in_bag_id: {best_in_bag_id}, best_in_bag_priority: {best_in_bag_priority}, best_in_mart_priority: {best_in_mart_priority}")
            if best_in_mart_id is None:
                print(f"ERR item_handler.py: scripted_buy_items(): best_in_mart_id is None")
                continue
            if best_in_bag_priority is not None:
                print(f"item_handler.py: scripted_buy_items(): best_in_bag_priority: {best_in_bag_priority}")
                if n_list == 0 and best_in_mart_priority - best_in_bag_priority > 1:
                    # having much better pokeball in bag, skip buying
                    # not sure what was supposed to go here???
                    continue
    
    def get_best_item_from_list(self, target_item_list, given_item_list):
        # return the best item in target_item_list that is in given_item_list
        # if no item in target_item_list is in given_item_list, return None
        # target_item_list and given_item_list are list of item id
        for item in target_item_list:
            if item in given_item_list:
                return item, target_item_list.index(item)
        return None, None
    
    def get_mart_items(self):
        x, y, current_map_id = self.nav.get_current_local_coords()
        map_id = int(current_map_id)
        # key format map_id@x,y
        dict_key = f'{map_id}@{x},{y}'
        if dict_key in MART_ITEMS_ID_DICT:
            mart_matched = MART_ITEMS_ID_DICT[dict_key]
            # match direction in mart_matched['dir']
            facing_direction = self.read_m(0xC109)  # wSpritePlayerStateData1FacingDirection
            direction = None
            if facing_direction == 0:  # down
                direction = 'down'
            elif facing_direction == 4:  # up
                direction = 'up'
            elif facing_direction == 8:  # left
                direction = 'left'
            elif facing_direction == 12:  # right
                direction = 'right'
            if direction is None:
                print(f'Warning: invalid facing direction: {facing_direction}')
                return None
            if direction == mart_matched['dir']:
                return mart_matched['items']    
        return None

    def get_item_price_by_id(self, item_id):
        # must have or error out
        return ITEM_TM_IDS_PRICES[item_id]
    
    def write_bcd(self, num):
        return ((num // 10) << 4) + (num % 10)
    
    def read_triple(self, start_add):
        return 256*256*self.read_m(start_add) + 256*self.read_m(start_add+1) + self.read_m(start_add+2)
    
    def read_bcd(self, num):
        return 10 * ((num >> 4) & 0x0f) + (num & 0x0f)
    
    def read_double(self, start_add):
        return 256*self.read_m(start_add) + self.read_m(start_add+1)
    
    def read_money(self):
        return (100 * 100 * self.read_bcd(self.read_m(0xD347)) + 
                100 * self.read_bcd(self.read_m(0xD348)) +
                self.read_bcd(self.read_m(0xD349)))
    
    def add_money(self, amount):
        if not amount:
            return
        money = self.read_money()
        # read_money() function
        # return (100 * 100 * self.read_bcd(self.read_m(0xD347)) + 
        #         100 * self.read_bcd(self.read_m(0xD348)) +
        #         self.read_bcd(self.read_m(0xD349)))
        # def read_bcd(self, num):
        #     return 10 * ((num >> 4) & 0x0f) + (num & 0x0f)
        if amount < 0:
            # deduct money
            money = max(0, money + amount)
        else:
            money += amount
        money = min(money, 999999)
        # self.pyboy.set_memory_value
        # it is in bcd format so need to convert to bcd
        write_mem(self.pyboy, 0xD347, self.write_bcd(money // 10000))
        write_mem(self.pyboy, 0xD348, self.write_bcd((money % 10000) // 100))
        write_mem(self.pyboy, 0xD349, self.write_bcd(money % 100))

    def sell_or_delete_item(self, is_sell, good_item_id=None):
        # bag full, delete 1 item
        # do not delete pokeballs and ALL_KEY_ITEMS
        # try to delete the last item first
        # if it is not the last item, swap with the last item
        # set the address after the last item to 255
        # set the address after the last quantity to 0
        
        items = self.get_items_in_bag()
        while len(items) > 0:
            tmp_item = items[-1]
            tmp_item_quantity = self.get_item_quantity(tmp_item)
            deleted = None
            for i in range(len(items) - 1, -1, -1):
                if items[i] not in ALL_GOOD_ITEMS:
                    if i == 19:
                        # delete the last item
                        write_mem(self.pyboy, 0xD31E + i*2, 0xff)
                        write_mem(self.pyboy, 0xD31F + i*2, 0)
                    else:
                        # swap with last item
                        write_mem(self.pyboy, 0xD31E + i*2, tmp_item)
                        write_mem(self.pyboy, 0xD31F + i*2, tmp_item_quantity)
                        # set last item to 255
                        write_mem(self.pyboy, 0xD31E + (len(items) - 1)*2, 0xff)
                        write_mem(self.pyboy, 0xD31F + (len(items) - 1)*2, 0)
                    # print(f'Delete item: {items[i]}')
                    deleted = items[i]
                    if is_sell:
                        self.add_money(self.get_item_price_by_id(deleted) // 2 * tmp_item_quantity)
                    # reset cache and get items again
                    self._items_in_bag = None
                    items = self.get_items_in_bag()
                    # 2 lines below replaced with bag class
                    # write_mem(self.pyboy, 0xD31D, len(items))
                    # # items_quantity = self.get_items_quantity_in_bag()
                    break
            if deleted is None:
                # no more item to delete
                break

        if good_item_id is not None:
            items = self.get_items_in_bag()
            if good_item_id in items:
                tmp_item = items[-1]
                tmp_item_quantity = self.get_item_quantity(tmp_item)
                idx = items.index(good_item_id)
                if idx == 19:
                    # delete the last item
                    write_mem(self.pyboy, 0xD31E + idx*2, 0xff)
                    write_mem(self.pyboy, 0xD31F + idx*2, 0)
                else:
                    # swap with last item
                    write_mem(self.pyboy, 0xD31E + idx*2, tmp_item)
                    write_mem(self.pyboy, 0xD31F + idx*2, tmp_item_quantity)
                    # set last item to 255
                    write_mem(self.pyboy, 0xD31E + 19*2, 0xff)
                    write_mem(self.pyboy, 0xD31F + 19*2, 0)
                # print(f'Delete item: {items[idx]}')
                deleted = good_item_id
                if is_sell:
                    self.add_money(self.get_item_price_by_id(deleted) // 2 * tmp_item_quantity)
                # reset cache and get items again
                self._items_in_bag = None
                items = self.get_items_in_bag()
                # 2 lines below replaced with bag class
                # write_mem(self.pyboy, 0xD31D, len(items))
                # items_quantity = self.get_items_quantity_in_bag()

    def _write_item_quantity_to_ram(self, item_id, quantity):
        """
        Directly writes the given quantity for a specific item_id to RAM.
        This is used to set, rather than increment, an item's quantity.
        """
        items_in_bag = self.get_items_in_bag()
        if item_id in items_in_bag:
            idx = list(items_in_bag.keys()).index(item_id)
            quantity_addr = 0xD31E + idx * 2 + 1 # Corrected: Quantity is at ID address + 1
            # Ensure the quantity does not exceed 255 for uint8_t
            clamped_quantity = min(quantity, 255)
            write_mem(self.pyboy, quantity_addr, clamped_quantity)
            # Read back immediately to verify the write
            verified_quantity = self.read_m(quantity_addr)
            print(f"[ITEM_HANDLER DEBUG] Verified write: Item {item_id} at {hex(quantity_addr)} now reads {verified_quantity} (attempted {clamped_quantity})")
            print(f"[ITEM_HANDLER DEBUG] Set quantity of item {item_id} at {hex(quantity_addr)} to {clamped_quantity}")
        else:
            print(f"[ITEM_HANDLER DEBUG] Cannot set quantity: Item {item_id} not found in bag.")

    def buy_item(self, item_id, quantity, price):        
        try:
            # add the item at the end of the bag
            # deduct money by price * quantity
            
            bag_items_dict = self.get_items_in_bag()
            
            if len(bag_items_dict) >= 20:
                # bag full
                print(f"[ITEM_HANDLER DEBUG] buy_item: Bag is full, cannot add item {hex(item_id)}")
                return False

            if item_id in bag_items_dict:
                new_quantity = bag_items_dict[item_id] + quantity
                self._write_item_quantity_to_ram(item_id, new_quantity)
                print(f"[ITEM_HANDLER DEBUG] buy_item: Updated quantity for {ITEM_ID_TO_NAME_DICT.get(item_id, 'UNKNOWN')}. New quantity: {new_quantity}")
            else:
                idx = len(bag_items_dict)
                item_id_addr = 0xD31E + idx*2
                quantity_addr = item_id_addr + 1 # Corrected: Quantity is at item_id_addr + 1
                
                print(f"[ITEM_HANDLER DEBUG] buy_item: Writing new item - ID: {hex(item_id)}, Quantity: {quantity}")
                print(f"[ITEM_HANDLER DEBUG] buy_item: Target Addresses - Item ID: {hex(item_id_addr)}, Quantity: {hex(quantity_addr)}")

                write_mem(self.pyboy, item_id_addr, item_id)
                write_mem(self.pyboy, quantity_addr, quantity)

                # Verify writes immediately
                verified_item_id = self.read_m(item_id_addr)
                verified_quantity = self.read_m(quantity_addr)
                print(f"[ITEM_HANDLER DEBUG] buy_item: Verified write - Item ID read: {hex(verified_item_id)}, Quantity read: {verified_quantity}")

                print(f"[ITEM_HANDLER DEBUG] buy_item: Added new item {ITEM_ID_TO_NAME_DICT.get(item_id, 'UNKNOWN')} (ID: {hex(item_id)}) with quantity {quantity} at address {hex(item_id_addr)}")
                # check if this is the last item in bag
                if idx != 19:
                    # if not then need to set the next item id to 255
                    next_item_id_addr = item_id_addr + 2
                    next_quantity_addr = next_item_id_addr + 1 # Corrected: Quantity is at next_item_id_addr + 1
                    
                    print(f"[ITEM_HANDLER DEBUG] buy_item: Setting next item slot to 0xff at address {hex(next_item_id_addr)}")
                    write_mem(self.pyboy, next_item_id_addr, 0xff)
                    write_mem(self.pyboy, next_quantity_addr, 0)

                    # Verify next slot writes immediately
                    verified_next_item_id = self.read_m(next_item_id_addr)
                    verified_next_quantity = self.read_m(next_quantity_addr)
                    print(f"[ITEM_HANDLER DEBUG] buy_item: Verified next slot write - Item ID read: {hex(verified_next_item_id)}, Quantity read: {verified_next_quantity}")

                    print(f"[ITEM_HANDLER DEBUG] buy_item: Set next item slot to 0xff at address {hex(next_item_id_addr)}")

            self.add_money(-price * quantity)
            # reset cache and get items again
            self._items_in_bag = None
            print(f"[ITEM_HANDLER DEBUG] buy_item: Item {ITEM_ID_TO_NAME_DICT.get(item_id, 'UNKNOWN')} successfully processed. Current money: {self.read_money()}")
            return True
        except Exception as e:
            print(f"ERR item_handler.py: buy_item(): {e}")
            return False

    def has_item(self, item_name: str) -> bool:
        """
        Check if the given item_name is in the bag.
        Accepts item_name as string; normalizes to Enum member.
        """
        key = item_name.upper().replace(' ', '_')
        try:
            item_enum = Items[key]
        except KeyError:
            print(f"ItemHandler: has_item unknown item '{item_name}'")
            return False
        return item_enum.value in self.get_items_in_bag()