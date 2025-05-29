# call a live Grok model, verify that on the very first turn it
# outputs each phase once and then we hit "Ready to play …"

import subprocess, json, time
def test_press_next_cycle():
    p = subprocess.Popen(
    ["python","-u","rewrite/recorder/play.py","--ai","--fast-video","--n_record","0"],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    phases = []
    seen_step = False
    start = time.time()
    while time.time()-start < 30:
        line = p.stdout.readline()
        if not line: break
        if "Phase:" in line:
            phases.append(line.strip())
        if "Ready to play" in line:
            seen_step = True
            break
    p.terminate()
    # We must see exactly once, in order:
    expected = [
    "Phase: Collecting state",
    "Phase: Prompting Grok",
    "Phase: Grok thinking…",
    "Phase: Grok responded →",
    "Phase: Executing action",
    ]
    for i, exp in enumerate(expected):
        assert phases[i].startswith(exp)
    assert seen_step