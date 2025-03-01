import os
import re
import pty
import sys
import time
import signal
import string
from datetime import datetime
from openai import OpenAI

client = OpenAI(
    api_key=os.environ["GEMINI_API_KEY"],
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
)

system_prompt = {"role": "system", "content": "You are playing Colossal Cave Adventure. 'User' is the game. Response format: 'move: word1 word2' (word2 is optional)."}
messages = [system_prompt]

prompt = string.Template("""\
<moves>
$moves
</moves>

<notes>
$notes
</notes>

You are playing Colossal Cave Adventure.
Above are the last 100 moves of the game, with the most recent move last.
Followed by that is your notes document, aka your scratch space. This serves as your short term memory; you should store everything of possible relevance here.
Decide on a move and return it, prefixed with "move:". Then update your notes and return them, prefixed with "notes:".
Make sure to think through each move, carefully considering all options and their possible outcomes before choosing a move.
If you're confused about how to do something, move on from it.
""")

now = int(time.time())
OUTFILE = open(f"logs/{now}.txt", 'w')
LOGFILE = open(f"logs/{now}.log", 'w')

def log(msg):
    print(f"{datetime.now()}: {msg}", file=LOGFILE, flush=True)

def out(msg):
    print(msg, file=OUTFILE, end='', flush=True)
    print(msg, end='', flush=True)

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
        log("requesting chat completion...")

        prompt_str = prompt.substitute(
            moves='\n\n'.join(map(lambda x: str(x), moves)),
            notes=notes
        )
        if extra_prompt:
            prompt_str += '\n\n' + extra_prompt
        log(f"prompt = {prompt_str}")

        response = client.chat.completions.create(
            model="gemini-2.0-pro-exp",
            n=1,
            messages=[{"role": "user", "content": prompt_str}],
            timeout=10
        )
        content = response.choices[0].message.content
        log(f"received chat completion ({content})...")
        if content is None:
            print("Error, running again: content is null", file=LOGFILE, flush=True)
            return get_move(moves, notes)

        content = content.strip('.,!?` \t')

        move_match = re.search(r'move:((\s*\w+\b)+)$', content, re.ASCII | re.MULTILINE)
        if not move_match or move_match.group(1) is None:
            log(f"Error, running again: bad formatting for move (content: {content}))")
            return get_move(moves, notes)
        new_move = move_match.group(1).strip()
        log(f"Parsed move as '{new_move}'")

        notes_match = re.search(r'notes:(.*)', content, re.DOTALL)
        if not notes_match or notes_match.group(1) is None:
            log(f"Error, running again: bad formatting for notes (content: {content}))")
            return get_move(moves, notes)
        new_notes = notes_match.group(1).strip()
        log(f"Parsed notes as '{new_notes}'")

        return new_move, new_notes
    except Exception as error:
        log(f"Error, waiting for 5 seconds (error: {error})")
        time.sleep(5)
        return get_move(moves, notes)


master, slave = pty.openpty()
ready = False

def send_move(move):
    cmd = (move + '\n').encode()
    os.write(master, cmd)

def read_output():
    while True:
        data = os.read(master, 1024)
        if not data:
            return
        chunk = data.decode()

def cleanup():
    os.close(master)
    LOGFILE.close()
    OUTFILE.close()

def signal_handler(sig, frame):
    global handling_signal

    if handling_signal:
        return

    handling_signal = True

    while not ready:
        time.sleep(0.1)

    os.write(master, b"\x04")
    score_buffer = ""
    while True:
        chunk = os.read(master, 1024).decode()
        if len(chunk) == 0 and len(score_buffer) > 0:
            break
        score_buffer += chunk
    score = score_buffer[3:].strip()
    out(f"\n\n{score}\n\n")

    cleanup()
    sys.exit(0)

handling_signal = False
signal.signal(signal.SIGINT, signal_handler)

pid = os.fork()
if pid == 0:
    os.close(master)
    os.setpgid(0, 0)
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    os.dup2(slave, 0)
    os.dup2(slave, 1)
    os.execvp("advent", ["advent"])
else:
    os.close(slave)
    moves = []
    notes = ""
    last_call = 0
    buffer = ""
    move = ""
    while True:
        try:
            output = os.read(master, 1024).decode()
            buffer += output

            if buffer.endswith("> "):
                ready = True
                out(buffer)
                result = buffer.lstrip(move).rstrip("> ").strip()
                moves.append(Move(move, result))

                now = time.time()
                if now - last_call < 4:
                    time.sleep(4 - (now - last_call))
                last_call = time.time()

                # log(f"calling get_move({notes}, {moves[-100:]})")
                move, notes = get_move(moves[-100:], notes)
                if not ready: # SIGINT
                    break
                ready = False
                send_move(move)

                buffer = ""
            elif output == "": # XXX delete?
                log("Done")
                break
        except OSError as e:
            log(f"Error: {e}")
            break

cleanup()
sys.exit(0)