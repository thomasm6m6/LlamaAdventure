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
    api_key=os.environ["GEMINI_API_KEY"],
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
)

prompt = string.Template("""\
<moves>
$moves
</moves>

<notes>
$notes
</notes>

You are playing Colossal Cave Adventure.
Above are the last 100 moves of the game, with the most recent move last.
Followed by that is your notes document, aka your scratch space.
This serves as your memory; you will have these notes across replays, so that you can adapt to the game.
You should use this space for notes that will help you later in the game, or on a future playthrough.
Decide on a move and return it, prefixed with "move:". Then update your notes and return them, prefixed with "notes:".
Make sure to think through each move, carefully considering all options and their possible outcomes before choosing a move.
If you're confused about how to do something, move on from it.

You can use the special move "!restart" to reset the game if you are truly stuck.
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
        if not notes_match or notes_match.group(1) is None:
            log.err(f"Error, running again: bad formatting for notes (content: {content}))")
            return get_move(moves, notes)
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
    notes = ""
    move = ""
    moves = []
    last_time = 0

    while True:
        output = controller.read()
        log.out(output)
        moves.append(Move(move, output))

        time_delta = time.time() - last_time
        if time_delta < 3:
            time.sleep(3 - time_delta)
        last_time = time.time()

        move, notes = get_move(moves, notes)
        controller.send(move)

if __name__ == "__main__":
    main()