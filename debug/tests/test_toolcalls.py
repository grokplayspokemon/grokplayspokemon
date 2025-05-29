from ..environment import RedGymEnv
from ..navigator import InteractiveNavigator
from ..toolcalls import dispatch_toolcall


# need to complete this file
# Button-press toolcalls
# Add tests in tests/unit/test_toolcalls.py that import your dispatch_toolcall,
# spin up a headless RedGymEnv + InteractiveNavigator, manually place the player
# somewhere on a known path, then:

import subprocess, json, time
def test_press_next_cycle():
    p = subprocess.Popen(
    ["python","-u","rewrite/recorder/play.py","--ai","--fast-video","--n_record","0"],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
before_idx = nav.current_coordinate_index
dispatch_toolcall({"name":"press_next","args":{}}, env, nav)
# Because press_next is PATH_FOLLOW, after one env.step we should advance:
assert nav.current_coordinate_index == before_idx+1