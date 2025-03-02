import fcntl
import os
import re
import select
import signal
import string
import subprocess
import sys
import time
from datetime import datetime
from openai import OpenAI

client = OpenAI(
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    api_key=os.environ["GEMINI_API_KEY"]
)

prompt = string.Template("""\
We are playing Colossal Cave Adventure; each person gets to pick one move. This is your turn.
Below are the last 50 moves, oldest to newest.
Following that are the notes from the person who chose the previous move.
Decide on a move and return it, prefixed with "move:".
Then, compile everything you know about the game, the current state, and your strategy to pass on to the next player, and return this prefixed with "notes:".
To make the best decision, list all the moves you can think of and the likely outcomes of each move, and choose the one that aligns most with furthering your progress in the game.
IMPORTANT: don't do the same thing repeatedly if it isn't working.

<MOVES>
$moves
</MOVES>

<NOTES>
$notes
</NOTES>
""")

prompt = string.Template("""\
You are an experienced adventurer exploring *Colossal Cave Adventure*. Your mission is to navigate the cave, find treasures, and overcome obstacles. Each turn, you pick one move. This is your turn.

Below are the last 10 moves (oldest to newest) and the notes from the previous player. Use this to decide your next move and update the notes.


**Recent Moves:**
$moves


**Previous Notes:**
$notes


**Instructions:**
1. **Review the Situation**: Think step-by-step:
   - What do the past moves tell you about your current location and progress?
   - What worked or didn't work before?
2. **Plan Your Move**: List possible actions you can take now and predict their outcomes. Choose the move that best advances your mission (e.g., exploring new areas, finding treasures).
3. **Check Your Strategy**: Does this move align with the previous notes? If not, adjust your plan.
4. **Update the Notes**:
   - **Current Situation:** Summarize your current location, immediate goals, and any obstacles.
   - **General Gameplay Strategies:** Add any new, reusable insights you've learned that could help in future sessions (e.g., "Keys are often found in side tunnels" or "Backtracking reveals hidden paths").
     - Review the previous notes and expand this section, keeping past strategies and adding new ones to build a knowledge base for future games.
     - These strategies will be used in future sessions to improve your gameplay, so focus on lessons that remain useful over time.

**Output Format:**
- "move: [your move]"
- "notes:
  - Current Situation: [details]
  - General Gameplay Strategies: [strategies]"

**Tip**: If a previous action (e.g., "hit the door") failed, try something new like searching for a key.
""")

class Log:
    def __init__(self):
        now = int(time.time())
        self.out_file = open(f"logs/{now}.txt", 'w')
        self.log_file = open(f"logs/{now}.log", 'w')

    def __del__(self):
        self.out_file.close()
        self.log_file.close()

    def out(self, msg):
        print(msg, file=self.out_file, end='', flush=True)
        print(msg, end='', flush=True)

    def err(self, msg):
        print(f"{datetime.now()}: {msg}", file=self.log_file, flush=True)

log = Log()

class Move:
    def __init__(self, move, result):
        self.move = move
        self.result = result

    def __str__(self):
        if self.move == "":
            return self.result
        return f"> {self.move}\n\n{self.result}"

def get_move(moves, notes, extra_prompt=""):
    try:
        log.err("requesting chat completion...")

        prompt_str = prompt.substitute(
            moves='\n\n'.join(map(lambda x: str(x), moves)),
            notes=notes
        )
        if extra_prompt:
            prompt_str += '\n\n' + extra_prompt
        log.err(f"prompt = {prompt_str}")

        response = client.chat.completions.create(
            model="gemini-2.0-flash-lite-preview",
            n=1,
            messages=[{"role": "user", "content": prompt_str}],
            timeout=10
        )
        content = response.choices[0].message.content
        log.err(f"received chat completion ({content})...")
        if content is None:
            log.err("Error, running again: content is null")
            return get_move(moves, notes)

        content = content.strip('.,!?` \t')

        move_match = re.search(r'move:((\s*\w+\b)+)$', content, re.ASCII | re.MULTILINE)
        if not move_match or move_match.group(1) is None:
            log.err(f"Error, running again: bad formatting for move (content: {content}))")
            return get_move(moves, notes)
        new_move = move_match.group(1).strip()
        log.err(f"Parsed move as '{new_move}'")

        notes_match = re.search(r'notes:(.*)', content, re.DOTALL)
        new_notes = ""
        if notes_match and notes_match.group(1):
            new_notes = notes_match.group(1).strip()
        log.err(f"Parsed notes as '{new_notes}'")

        return new_move, new_notes
    except Exception as error:
        log.err(f"Error, waiting for 5 seconds (error: {error})")
        time.sleep(5)
        return get_move(moves, notes)

class GameController:
    def __init__(self):
        self.writing = False
        self.proc = subprocess.Popen(
            ["stdbuf", "-o0", "advent"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            preexec_fn=os.setpgrp
        )
        fd = self.proc.stdout.fileno()
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        time.sleep(0.1)

    def send(self, cmd):
        while self.writing:
            time.sleep(0.1)
        self.writing = True
        # if cmd == "!reset":
        self.proc.stdin.write(cmd + '\n')
        if not self.proc.stdin.closed:
            self.proc.stdin.flush()
        self.writing = False

    def read(self):
        while True:
            readable, _, _ = select.select([self.proc.stdout], [], [], 0.1)
            if readable:
                break

        buffer = ""
        while True:
            try:
                chunk = self.proc.stdout.read()
                if not chunk:
                    break
                buffer += chunk
            except TypeError:
                break

        buffer2 = []
        for line in buffer.split('\n'):
            if not line.startswith("> "):
                buffer2.append(line)

        buffer = '\n'.join(buffer2).strip()
        return buffer

    def stop(self):
        self.send("quit\nyes")
        score = self.read()
        self.proc.terminate()
        return score + '\n'

def signal_handler(sig, frame):
    global handling_signal, controller

    if handling_signal:
        return
    handling_signal = True

    score = controller.stop()
    log.out(score)
    sys.exit(0)

handling_signal = False
signal.signal(signal.SIGINT, signal_handler)

controller = GameController()

def main():
    last_time = 0
    cmd = ""
    moves = []
    notes = ""

    while True:
        output = controller.read()
        move = Move(cmd, output)
        moves.append(move)
        log.out(f"{move}\n\n")

        time_delta = time.time() - last_time
        if time_delta < 4:
            time.sleep(4 - time_delta)
        last_time = time.time()

        cmd, notes = get_move(moves[-10:], notes)
        controller.send(cmd)

if __name__ == "__main__":
    main()