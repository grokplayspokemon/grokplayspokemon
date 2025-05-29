
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

from ram_map import read_m, write_mem
from data.constants import ALL_GOOD_ITEMS, GOOD_ITEMS_PRIORITY, \
    POKEBALL_PRIORITY, POTION_PRIORITY, REVIVE_PRIORITY
from data.constants import MART_ITEMS_ID_DICT, ITEM_TM_IDS_PRICES
from data.ram_addresses import RamAddress as RAM

from navigator import InteractiveNavigator
from global_map import local_to_global, global_to_local
from functools import partial

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
        
    def init_caches(self):
        # for cached properties
        self._all_events_string = ''
        self._battle_type = None
        self._cur_seen_map = None
        self._minimap_warp_obs = None
        self._is_warping = None
        self._items_in_bag = None
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
    
    def get_items_in_bag(self, one_indexed=0):
        if self._items_in_bag is None:
            first_item = 0xD31E
            # total 20 items
            # item1, quantity1, item2, quantity2, ...
            item_ids = []
            for i in range(0, 40, 2):
                item_id = self.read_m(first_item + i)
                if item_id == 0 or item_id == 0xff:
                    break
                item_ids.append(item_id)
            self._items_in_bag = item_ids
        else:
            item_ids = self._items_in_bag
        if one_indexed:
            return [i + 1 for i in item_ids]
        return item_ids
    
    def get_items_quantity_in_bag(self):
        first_quantity = 0xD31E
        # total 20 items
        # quantity1, item2, quantity2, ...
        item_quantities = []
        for i in range(1, 40, 2):
            item_quantity = self.read_m(first_quantity + i)
            if item_quantity == 0 or item_quantity == 0xff:
                break
            item_quantities.append(item_quantity)
        return item_quantities
    
    def scripted_manage_items(self):
        items = self.get_items_in_bag()
        if self._last_item_count == len(items):
            return
        
        if self.read_m("wIsInBattle") > 0 or self.read_m(0xFFB0) == 0:  # hWY in menu
            return
        
        # pokeballs = [0x01, 0x02, 0x03, 0x04]

        if len(items) == 20:
            # bag full, delete 1 item
            # do not delete pokeballs and ALL_KEY_ITEMS
            # try to delete the last item first
            # if it is not the last item, swap with the last item
            # set the address after the last item to 255
            # set the address after the last quantity to 0
            tmp_item = items[-1]
            tmp_item_quantity = self.get_items_quantity_in_bag()[-1]
            
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
                    # print(f'Delete item: {items[i]}')
                    deleted = True
                    break

            if not deleted:
                # print(f'Warning: no item deleted, bag full, delete good items instead')
                # delete good items if no other items
                # from first to last good items priority
                for good_item in GOOD_ITEMS_PRIORITY:
                    if good_item in items:
                        idx = items.index(good_item)
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
                        deleted = True
                        break

            # reset cache and get items again
            self._items_in_bag = None
            items = self.get_items_in_bag()
            write_mem(self.pyboy, 0xD31D, len(items))
        
        item_idx_ptr = 0
        # sort good items to the front based on priority
        for good_item in GOOD_ITEMS_PRIORITY:
            if good_item in items:
                all_items_quantity = self.get_items_quantity_in_bag()
                idx = items.index(good_item)
                if idx == item_idx_ptr:
                    # already in the correct position
                    item_idx_ptr += 1
                    continue
                cur_item_quantity = all_items_quantity[idx]
                tmp_item = items[item_idx_ptr]
                tmp_item_quantity = all_items_quantity[item_idx_ptr]
                # print(f'Swapping item {item_idx_ptr}:{tmp_item}/{tmp_item_quantity} with {idx}:{good_item}/{cur_item_quantity}')
                # swap
                write_mem(self.pyboy, 0xD31E + item_idx_ptr*2, good_item)
                write_mem(self.pyboy, 0xD31F + item_idx_ptr*2, cur_item_quantity)
                write_mem(self.pyboy, 0xD31E + idx*2, tmp_item)
                write_mem(self.pyboy, 0xD31F + idx*2, tmp_item_quantity)
                item_idx_ptr += 1
                # reset cache and get items again
                self._items_in_bag = None
                items = self.get_items_in_bag()
                # print(f'Moved good item: {good_item} to pos: {item_idx_ptr}')
        self._last_item_count = len(self.get_items_in_bag())

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
            items_quantity = self.get_items_quantity_in_bag()
            # if len(items) == 0:
            #     break
            tmp_item = items[-1]
            tmp_item_quantity = items_quantity[-1]
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
                        self.add_money(self.get_item_price_by_id(deleted) // 2 * items_quantity[i])
                    # reset cache and get items again
                    self._items_in_bag = None
                    items = self.get_items_in_bag()
                    write_mem(self.pyboy, 0xD31D, len(items))
                    # items_quantity = self.get_items_quantity_in_bag()
                    break
            if deleted is None:
                # no more item to delete
                break

        if good_item_id is not None:
            items = self.get_items_in_bag()
            if good_item_id in items:
                items_quantity = self.get_items_quantity_in_bag()
                tmp_item = items[-1]
                tmp_item_quantity = items_quantity[-1]
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
                    self.add_money(self.get_item_price_by_id(deleted) // 2 * items_quantity[idx])
                # reset cache and get items again
                self._items_in_bag = None
                items = self.get_items_in_bag()
                write_mem(self.pyboy, 0xD31D, len(items))
                # items_quantity = self.get_items_quantity_in_bag()

    def buy_item(self, item_id, quantity, price):
        # add the item at the end of the bag
        # deduct money by price * quantity
        bag_items = self.get_items_in_bag()
        bag_items_quantity = self.get_items_quantity_in_bag()
        if len(bag_items) >= 20:
            # bag full
            return
        if item_id in bag_items:
            idx = bag_items.index(item_id)
            bag_items_quantity[idx] += quantity
            write_mem(self.pyboy, 0xD31F + idx*2, bag_items_quantity[idx])
        else:
            idx = len(bag_items)
            write_mem(self.pyboy, 0xD31E + idx*2, item_id)
            write_mem(self.pyboy, 0xD31F + idx*2, quantity)
            # check if this is the last item in bag
            if idx != 19:
                # if not then need to set the next item id to 255
                write_mem(self.pyboy, 0xD31E + idx*2 + 2, 0xff)
                write_mem(self.pyboy, 0xD31F + idx*2 + 2, 0)
        self.add_money(-price * quantity)
        # reset cache and get items again
        self._items_in_bag = None

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
        bag_items = self.get_items_in_bag()
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
            best_in_bag_idx = bag_items.index(best_in_bag_id) if best_in_bag_id is not None else None
            best_in_bag_quantity = self.get_items_quantity_in_bag()[best_in_bag_idx] if best_in_bag_idx is not None else None
            print(f"item_handler.py: scripted_buy_items(): best_in_mart_id: {best_in_mart_id}, best_in_bag_id: {best_in_bag_id}, best_in_bag_priority: {best_in_bag_priority}, best_in_mart_priority: {best_in_mart_priority}")
            if best_in_mart_id is None:
                print(f"ERR item_handler.py: scripted_buy_items(): best_in_mart_id is None")
                continue
            if best_in_bag_priority is not None:
                print(f"item_handler.py: scripted_buy_items(): best_in_bag_priority: {best_in_bag_priority}")
                if n_list == 0 and best_in_mart_priority - best_in_bag_priority > 1:
                    # having much better pokeball in bag, skip buying
                    print(f"item_handler.py: scripted_buy_items(): having much better pokeball in bag, skip buying")
                    continue
                elif n_list == 1 and best_in_mart_priority - best_in_bag_priority > 2:
                    # having much better potion in bag, skip buying
                    print(f"item_handler.py: scripted_buy_items(): having much better potion in bag, skip buying")
                    continue
                # revive only have 2 types so ok to buy if insufficient
                if best_in_bag_id is not None and best_in_bag_priority < best_in_mart_priority and best_in_bag_quantity >= target_quantity:
                    # already have better item in bag with desired quantity
                    print(f"item_handler.py: scripted_buy_items(): already have better item in bag with desired quantity")
                    continue
                if best_in_bag_quantity is not None and best_in_bag_priority == best_in_mart_priority and best_in_bag_quantity >= target_quantity:
                    # same item
                    # and already have enough
                    print(f"item_handler.py: scripted_buy_items(): same item and already have enough")
                    continue
            item_price = self.get_item_price_by_id(best_in_mart_id)
            print(f"item_handler.py: scripted_buy_items(): item_price: {item_price}")

            # # commented to cut down on complexity
            # # try to sell items
            # if best_in_bag_priority is not None and best_in_bag_priority > best_in_mart_priority:
            #     # having worse item in bag, sell it
            #     if n_list == 0 and best_in_bag_priority - best_in_mart_priority > 1:
            #         # having much worse pokeball in bag
            #         self.sell_or_delete_item(is_sell=True, good_item_id=best_in_bag_id)
            #     elif n_list == 1 and best_in_bag_priority - best_in_mart_priority > 2:
            #         # having much worse potion in bag
            #         self.sell_or_delete_item(is_sell=True, good_item_id=best_in_bag_id)
            # else:
            #     self.sell_or_delete_item(is_sell=True)

            # get items again
            bag_items = self.get_items_in_bag()
            if best_in_mart_id not in bag_items and len(bag_items) >= 19:
                # is new item and bag is full
                # bag is full even after selling
                break
            if self.read_money() < item_price:
                print(f"item_handler.py: scripted_buy_items(): not enough money")
                # not enough money
                continue
            if best_in_bag_quantity is None:
                print(f"item_handler.py: scripted_buy_items(): best_in_bag_quantity is None")
                needed_quantity = target_quantity
            elif best_in_bag_priority == best_in_mart_priority:
                # item in bag is same
                print(f"item_handler.py: scripted_buy_items(): item in bag is same")
                needed_quantity = target_quantity - best_in_bag_quantity
            elif best_in_bag_priority > best_in_mart_priority:
                # item in bag is worse
                print(f"item_handler.py: scripted_buy_items(): item in bag is worse")
                needed_quantity = target_quantity
            elif best_in_bag_priority < best_in_mart_priority:
                # item in bag is better, but not enough quantity
                print(f"item_handler.py: scripted_buy_items(): item in bag is better, but not enough quantity")
                if best_in_mart_id in bag_items:
                    mart_item_in_bag_idx = bag_items.index(best_in_mart_id)
                    needed_quantity = target_quantity - self.get_items_quantity_in_bag()[mart_item_in_bag_idx] - best_in_bag_quantity
                    print(f"item_handler.py: scripted_buy_items(): needed_quantity: {needed_quantity}")
                else:   
                    needed_quantity = target_quantity - best_in_bag_quantity
                    print(f"item_handler.py: scripted_buy_items(): needed_quantity: {needed_quantity}")
            if needed_quantity < 1:
                # already have enough
                print(f"item_handler.py: scripted_buy_items(): already have enough")
                continue
            affordable_quantity = min(needed_quantity, (self.read_money() // item_price))
            print(f"item_handler.py: scripted_buy_items(): affordable_quantity: {affordable_quantity}")
            self.buy_item(best_in_mart_id, affordable_quantity, item_price)
            # reset cache and get items again
            self._items_in_bag = None
            bag_items = self.get_items_in_bag()
            write_mem(self.pyboy, 0xD31D, len(bag_items))
            write_mem(self.pyboy, RAM.wBagSavedMenuItem.value, 0x0)
            # print(f'Bought item: {best_in_mart_id} x {affordable_quantity}')
        self.use_mart_count += 1
        # reset item count to trigger scripted_manage_items
        self._last_item_count = 0
        return True