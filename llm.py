import os
import re
import pty
import sys
import time
import signal
from datetime import datetime
from openai import OpenAI

# TODO: maintain a plan external to the message history.

client = OpenAI(
    api_key=os.environ["GEMINI_API_KEY"],
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
)

system_prompt = {"role": "system", "content": "You are playing Colossal Cave Adventure. 'User' is the game. Response format: 'move: word1 word2' (word2 is optional)."}
messages = [system_prompt]

now = int(time.time())
OUTFILE = open(f"logs/{now}.txt", 'w')
LOGFILE = open(f"logs/{now}.log", 'w')

def log(msg):
    print(f"{datetime.now()}: {msg}", file=LOGFILE, flush=True)

def out(msg):
    print(msg, file=OUTFILE, end='', flush=True)
    print(msg, end='', flush=True)

class Message:
    def __init__(self, role, content):
        self.role = role
        self.content = content

    def asDict(self):
        return [{"role": self.role, "content": self.content}]

def get_move(messages):
    try:
        log("requesting chat completion...")
        response = client.chat.completions.create(
            model="gemini-2.0-flash-thinking-exp",
            n=1,
            messages=messages,
            timeout=10
        )
        content = response.choices[0].message.content
        log(f"received chat completion ({content})...")
        if content is None:
            print("Error, running again: content is null", file=LOGFILE, flush=True)
            return get_move(messages)

        content = content.strip('.,!?` \t')
        match = re.search(r'move:((\s+\w+\b)+)$', content, re.ASCII)
        if not match or match.group(1) is None:
            log(f"Error, running again: move unspecified (content: {content})")
            move_msg = Message("assistant", match.group() if match else "")
            resp_msg = Message("user", "ERROR: badly formatted move.")
            return get_move(messages + [move_msg.asDict(), resp_msg.asDict()])

        cmd = match.group(1).lstrip()
        log(f"Parsed move as '{cmd}'")
        return cmd
    except Exception as error:
        log(f"Error, waiting for 5 seconds (error: {error})")
        time.sleep(5)
        return get_move(messages)


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
    last_call = 0
    buffer = ""
    while True:
        try:
            output = os.read(master, 1024).decode()
            buffer += output

            if buffer.endswith("> "):
                ready = True
                out(buffer)
                messages.append({
                    "role": "user",
                    "content": buffer.rstrip("> ").strip()
                })

                now = time.time()
                if now - last_call < 3:
                    time.sleep(3 - (now - last_call))
                last_call = time.time()

                move = get_move(messages)
                if not ready: # SIGINT
                    break
                ready = False
                send_move(move)

                messages.append({
                    "role": "assistant",
                    "content": "move: " + move
                })
                buffer = ""
            elif output == "":
                log("Done")
                break
        except OSError as e:
            log(f"Error: {e}")
            break

cleanup()
sys.exit(0)