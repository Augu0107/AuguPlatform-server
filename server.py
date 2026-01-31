import socket
import threading
import json
import os
import uuid

# =========================
# DEFAULT FILES
# =========================

DEFAULT_CONFIG = {
    "host": "0.0.0.0",
    "port": 12345,
    "max_players": 10,
    "password_server": 0,
    "world-name": "world",
    "server-motd": "A simple server",
    "server-name": "My server"
}

DEFAULT_COMMANDS = {
    "ban": 2,
    "mute": 1,
    "unpunish": 2,
    "kick": 1,
    "perms": 3,
    "help": 0,
    "stop": 3
}

# =========================
# UTILITY FUNCTION
# =========================

def load_or_create(path, default):
    if not os.path.exists(path):
        with open(path, "w") as f:
            json.dump(default, f, indent=4)
        print(f"[SERVER] Created {path}")
    with open(path) as f:
        return json.load(f)

# =========================
# LOAD FILES
# =========================

config = load_or_create("config.json", DEFAULT_CONFIG)
blacklist = load_or_create("blacklist.json", {})
permissions = load_or_create("permission.json", {})

# Load commands.json and sync with defaults
command_perms = load_or_create("commands.json", DEFAULT_COMMANDS)
# Remove old commands not in defaults
command_perms = {cmd: lvl for cmd, lvl in command_perms.items() if cmd in DEFAULT_COMMANDS}
# Add missing commands from defaults
for cmd, lvl in DEFAULT_COMMANDS.items():
    if cmd not in command_perms:
        command_perms[cmd] = lvl
# Save updated commands.json
with open("commands.json", "w") as f:
    json.dump(command_perms, f, indent=4)

# =========================
# WORLD SETUP
# =========================

WORLD_NAME = config["world-name"]
WORLD_DIR = f"worlds/{WORLD_NAME}"
WORLD_PATH = f"{WORLD_DIR}/world.json"
os.makedirs(WORLD_DIR, exist_ok=True)

if not os.path.exists(WORLD_PATH):
    world = [["air"] * 10 for _ in range(10)]
    with open(WORLD_PATH, "w") as f:
        json.dump(world, f)
    print(f"[SERVER] Created new world at {WORLD_PATH}")
else:
    with open(WORLD_PATH) as f:
        world = json.load(f)

# =========================
# SERVER STATE
# =========================

clients = {}  # player_id -> socket
lock = threading.Lock()

# =========================
# PERMISSIONS
# =========================

def get_level(pid):
    return permissions.get(pid, 0)

def can_execute(pid, cmd):
    if pid == "CONSOLE":
        return True
    return get_level(pid) >= command_perms.get(cmd, 999)

# =========================
# BLACKLIST / PUNISHMENTS
# =========================

def save_blacklist():
    with open("blacklist.json", "w") as f:
        json.dump(blacklist, f, indent=4)

def ban(pid):
    blacklist[pid] = "banned"
    save_blacklist()

def mute(pid):
    blacklist[pid] = "muted"
    save_blacklist()

def unpunish(pid):
    if pid in blacklist:
        blacklist.pop(pid)
        save_blacklist()

# =========================
# WORLD
# =========================

def save_world():
    with open(WORLD_PATH, "w") as f:
        json.dump(world, f)

# =========================
# PLAYER ACTIONS
# =========================

def kick(pid, reason="Kicked"):
    if pid in clients:
        try:
            clients[pid].send(json.dumps({
                "type": "disconnect",
                "reason": reason
            }).encode())
            clients[pid].close()
        except:
            pass
        clients.pop(pid, None)

def set_perm(pid, level):
    permissions[pid] = level
    with open("permission.json", "w") as f:
        json.dump(permissions, f, indent=4)

# =========================
# COMMAND HANDLER
# =========================

def handle_command(sender, cmdline):
    parts = cmdline.split()
    if not parts:
        return
    cmd = parts[0][1:]

    if cmd not in command_perms:
        print(f"[SERVER] Command '{cmd}' does not exist.")
        return

    if not can_execute(sender, cmd):
        print(f"[SERVER] You do not have permission to execute '{cmd}'.")
        return

    try:
        if cmd == "kick":
            if len(parts) < 2:
                print("[SERVER] Usage: /kick <player_id>")
                return
            kick(parts[1])
            print(f"[SERVER] Player {parts[1]} kicked.")

        elif cmd == "ban":
            if len(parts) < 2:
                print("[SERVER] Usage: /ban <player_id>")
                return
            ban(parts[1])
            print(f"[SERVER] Player {parts[1]} banned.")

        elif cmd == "mute":
            if len(parts) < 2:
                print("[SERVER] Usage: /mute <player_id>")
                return
            mute(parts[1])
            print(f"[SERVER] Player {parts[1]} muted.")

        elif cmd == "unpunish":
            if len(parts) < 2:
                print("[SERVER] Usage: /unpunish <player_id>")
                return
            unpunish(parts[1])
            print(f"[SERVER] Player {parts[1]} unpunished.")

        elif cmd == "perms":
            if len(parts) < 3:
                print("[SERVER] Usage: /perms <player_id> <level>")
                return
            set_perm(parts[1], int(parts[2]))
            print(f"[SERVER] Player {parts[1]}'s permission set to {parts[2]}.")

        elif cmd == "help":
            print("[SERVER] Available commands:")
            for c in command_perms:
                print(f"  /{c}")

        elif cmd == "stop":
            print("[SERVER] Stopping server safely...")
            save_world()
            save_blacklist()
            with open("permission.json", "w") as f:
                json.dump(permissions, f, indent=4)
            print("[SERVER] All data saved. Goodbye!")
            os._exit(0)

        else:
            print(f"[SERVER] Command '{cmd}' is not implemented yet.")

    except Exception as e:
        print(f"[SERVER] Error executing command '{cmd}': {e}")

# =========================
# CLIENT THREAD
# =========================

def client_thread(client, addr):
    pid = str(uuid.uuid4())[:6]

    if blacklist.get(pid) == "banned":
        client.close()
        return

    with lock:
        clients[pid] = client

    try:
        client.send(json.dumps({
            "type": "welcome",
            "id": pid,
            "motd": config["server-motd"],
            "server": config["server-name"],
            "world": world
        }).encode())
    except:
        clients.pop(pid, None)
        return

    while True:
        try:
            data = client.recv(4096)
            if not data:
                break

            msg = json.loads(data.decode())

            if msg["type"] == "command":
                if blacklist.get(pid) != "muted":
                    handle_command(pid, msg["command"])

            elif msg["type"] == "break_block":
                x, y = msg["x"], msg["y"]
                if 0 <= y < len(world) and 0 <= x < len(world[0]):
                    world[y][x] = "air"
                    save_world()

        except:
            break

    with lock:
        clients.pop(pid, None)
    client.close()

# =========================
# CONSOLE THREAD
# =========================

def console():
    while True:
        cmd = input(">>> ")
        if cmd.startswith("/"):
            handle_command("CONSOLE", cmd)

# =========================
# START SERVER
# =========================

server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.bind((config["host"], config["port"]))
server.listen(config["max_players"])

print(f"[SERVER] {config['server-name']} started on {config['host']}:{config['port']}")

threading.Thread(target=console, daemon=True).start()

while True:
    c, a = server.accept()
    threading.Thread(target=client_thread, args=(c, a), daemon=True).start()
