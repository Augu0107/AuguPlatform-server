import socket
import threading
import json
import os
import uuid
import time
import struct

# =========================
# HELPER FUNCTIONS
# =========================

def send_msg(sock, msg_dict):
    """Send a length-prefixed JSON message"""
    msg_json = json.dumps(msg_dict)
    msg_bytes = msg_json.encode('utf-8')
    msg_len = len(msg_bytes)
    # Send 4-byte length prefix, then the message
    sock.sendall(struct.pack('!I', msg_len) + msg_bytes)

def recv_msg(sock):
    """Receive a length-prefixed JSON message"""
    # Read 4-byte length prefix
    raw_msglen = recv_all(sock, 4)
    if not raw_msglen:
        return None
    msglen = struct.unpack('!I', raw_msglen)[0]
    # Read the message data
    msg_bytes = recv_all(sock, msglen)
    if not msg_bytes:
        return None
    return json.loads(msg_bytes.decode('utf-8'))

def recv_all(sock, n):
    """Helper to receive exactly n bytes"""
    data = bytearray()
    while len(data) < n:
        packet = sock.recv(n - len(data))
        if not packet:
            return None
        data.extend(packet)
    return bytes(data)

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
    "server-name": "My server",
    "console_mode": "interactive"  # "interactive" or "pterodactyl"
}

DEFAULT_COMMANDS = {
    "ban": 2,
    "mute": 1,
    "unpunish": 2,
    "kick": 1,
    "perms": 3,
    "help": 0,
    "stop": 3,
    "respawn": 0,
    "tp": 0,
    "give": 0,
    "clear": 0  # Clear inventory and hotbar
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
PLAYERS_PATH = f"{WORLD_DIR}/players.json"
os.makedirs(WORLD_DIR, exist_ok=True)

if not os.path.exists(WORLD_PATH):
    # Create a more interesting world with terrain layers
    world = []
    world_width = 100
    world_height = 30
    
    for y in range(world_height):
        row = []
        for x in range(world_width):
            # Top layers: air
            if y < 5:
                row.append("air")
            # Grass layer
            elif y == 5:
                row.append("grass")
            # Dirt layers
            elif y >= 6 and y < 10:
                row.append("dirt")
            # Sand/dirt mix
            elif y >= 10 and y < 15:
                # Add some sand patches
                if x % 7 == 0 or x % 11 == 0:
                    row.append("sand")
                else:
                    row.append("dirt")
            # Stone layers
            elif y >= 15 and y < world_height - 1:
                row.append("stone")
            # Bedrock at bottom (unbreakable)
            else:
                row.append("bedrock")
        world.append(row)
    
    # Generate trees on grass
    import random
    random.seed(42)  # Consistent trees
    for x in range(5, world_width - 5):  # Leave margins
        # 10% chance of tree on each grass block
        if random.random() < 0.1:
            # Tree structure: log trunk (3 blocks) + leaves (5x3 top)
            trunk_height = 3
            
            # Place trunk (log blocks)
            for trunk_y in range(3):  # y=2,3,4 (above grass at y=5)
                if world[5 - trunk_y - 1][x] == "air":
                    world[5 - trunk_y - 1][x] = "log"
            
            # Place leaves (5 wide, 3 tall, centered on top of trunk)
            leaves_y_start = 5 - trunk_height - 2  # Top of leaves
            for ly in range(3):  # 3 rows of leaves
                leaves_y = leaves_y_start + ly
                if leaves_y >= 0:
                    for lx in range(-2, 3):  # 5 wide (-2, -1, 0, 1, 2)
                        leaves_x = x + lx
                        if 0 <= leaves_x < world_width and world[leaves_y][leaves_x] == "air":
                            world[leaves_y][leaves_x] = "leaves"
    
    with open(WORLD_PATH, "w") as f:
        json.dump(world, f)
    print(f"[SERVER] Created new world with trees at {WORLD_PATH}")
else:
    with open(WORLD_PATH) as f:
        world = json.load(f)

# Load player data (positions, hotbars)
if not os.path.exists(PLAYERS_PATH):
    player_data = {}
    with open(PLAYERS_PATH, "w") as f:
        json.dump(player_data, f)
else:
    with open(PLAYERS_PATH) as f:
        player_data = json.load(f)

# =========================
# SERVER STATE
# =========================

clients = {}  # player_id -> socket
player_positions = {}  # player_id -> (x, y)
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

def save_players():
    with open(PLAYERS_PATH, "w") as f:
        json.dump(player_data, f)

# =========================
# PLAYER ACTIONS
# =========================

def kick(pid, reason="Kicked"):
    if pid in clients:
        try:
            send_msg(clients[pid], {
                "type": "disconnect",
                "reason": reason
            })
            clients[pid].close()
        except:
            pass
        clients.pop(pid, None)

def set_perm(pid, level):
    permissions[pid] = level
    with open("permission.json", "w") as f:
        json.dump(permissions, f, indent=4)

# =========================
# BROADCAST
# =========================

def broadcast(msg, exclude=None):
    with lock:
        for pid, sock in clients.items():
            if pid != exclude:
                try:
                    send_msg(sock, msg)
                except:
                    pass

# =========================
# COMMAND HANDLER
# =========================

def handle_command(sender, cmdline):
    parts = cmdline.split()
    if not parts:
        return None
    cmd = parts[0][1:]

    if cmd not in command_perms:
        return f"Command '{cmd}' does not exist."

    if not can_execute(sender, cmd):
        return f"You do not have permission to execute '{cmd}'."

    try:
        if cmd == "kick":
            if len(parts) < 2:
                return "Usage: /kick <player_id>"
            kick(parts[1])
            return f"Player {parts[1]} kicked."

        elif cmd == "ban":
            if len(parts) < 2:
                return "Usage: /ban <player_id>"
            ban(parts[1])
            # Kick if online
            if parts[1] in clients:
                kick(parts[1], "You have been banned")
            return f"Player {parts[1]} banned."

        elif cmd == "mute":
            if len(parts) < 2:
                return "Usage: /mute <player_id>"
            mute(parts[1])
            return f"Player {parts[1]} muted."

        elif cmd == "unpunish":
            if len(parts) < 2:
                return "Usage: /unpunish <player_id>"
            unpunish(parts[1])
            return f"Player {parts[1]} unpunished."

        elif cmd == "perms":
            if len(parts) < 3:
                return "Usage: /perms <player_id> <level>"
            set_perm(parts[1], int(parts[2]))
            return f"Player {parts[1]}'s permission set to {parts[2]}."

        elif cmd == "help":
            return "Available commands: " + ", ".join([f"/{c}" for c in command_perms])

        elif cmd == "respawn":
            if sender == "CONSOLE":
                return "This command cannot be used from console."
            # Respawn player
            if sender in player_data:
                player_data[sender]["x"] = 10
                player_data[sender]["y"] = 3
                save_players()
                # Send respawn packet
                if sender in clients:
                    send_msg(clients[sender], {
                        "type": "respawn",
                        "x": 10,
                        "y": 3
                    })
                return "You have been respawned."
            return "Error respawning."

        elif cmd == "tp":
            if sender == "CONSOLE":
                return "This command cannot be used from console."
            if len(parts) < 2:
                return "Usage: /tp <player_id>"
            
            target_id = parts[1]
            
            # Check if target exists
            if target_id not in player_data:
                return f"Player {target_id} not found."
            
            # Get target position
            target_x = player_data[target_id]["x"]
            target_y = player_data[target_id]["y"]
            
            # Teleport sender to target
            player_data[sender]["x"] = target_x
            player_data[sender]["y"] = target_y
            player_positions[sender] = (target_x, target_y)
            save_players()
            
            # Send respawn packet to update position
            if sender in clients:
                send_msg(clients[sender], {
                    "type": "respawn",
                    "x": target_x,
                    "y": target_y
                })
            
            return f"Teleported to {target_id} at ({target_x}, {target_y})."

        elif cmd == "give":
            if sender == "CONSOLE":
                return "This command cannot be used from console."
            if len(parts) < 3:
                return "Usage: /give <block_type> <quantity>"
            
            block_type = parts[1]
            try:
                quantity = int(parts[2])
            except:
                return "Quantity must be a number."
            
            if quantity <= 0 or quantity > 999:
                return "Quantity must be between 1 and 999."
            
            # Valid block types
            valid_blocks = ["dirt", "grass", "stone", "sand", "wood", "ladder", "log", "leaves"]
            if block_type not in valid_blocks:
                return f"Invalid block type. Valid: {', '.join(valid_blocks)}"
            
            # Add to player's hotbar (first empty slot or stack)
            if sender in player_data:
                hotbar = player_data[sender]["hotbar"]
                original_quantity = quantity
                
                # Try to stack in existing slots (max 64 per slot)
                for i, slot in enumerate(hotbar):
                    if slot and slot["block"] == block_type and quantity > 0:
                        space_available = 64 - slot["count"]
                        if space_available > 0:
                            add_amount = min(quantity, space_available)
                            slot["count"] += add_amount
                            quantity -= add_amount
                
                # Fill empty slots with remaining quantity (max 64 per slot)
                for i, slot in enumerate(hotbar):
                    if slot is None and quantity > 0:
                        add_amount = min(quantity, 64)
                        hotbar[i] = {"block": block_type, "count": add_amount}
                        quantity -= add_amount
                
                # Save and update client once at the end
                if original_quantity > quantity:  # Something was added
                    save_players()
                    if sender in clients:
                        send_msg(clients[sender], {
                            "type": "hotbar_update",
                            "hotbar": hotbar
                        })
                    
                    added = original_quantity - quantity
                    if quantity > 0:
                        return f"Added {added}/{original_quantity} {block_type}. Hotbar full, {quantity} items couldn't fit."
                    return f"Added {added} {block_type} to your hotbar."
                
                return "Hotbar is full!"
            
            return "Error giving items."
        
        elif cmd == "clear":
            if sender == "CONSOLE":
                return "This command cannot be used from console."
            
            # Clear hotbar and inventory
            if sender in player_data:
                player_data[sender]["hotbar"] = [None] * 7
                player_data[sender]["inventory"] = [None] * 21
                save_players()
                
                # Update client
                if sender in clients:
                    send_msg(clients[sender], {
                        "type": "hotbar_update",
                        "hotbar": player_data[sender]["hotbar"]
                    })
                    send_msg(clients[sender], {
                        "type": "inventory_update",
                        "inventory": player_data[sender]["inventory"]
                    })
                
                return "Cleared your inventory and hotbar!"
            
            return "Error clearing inventory."

        elif cmd == "stop":
            print("[SERVER] Stopping server safely...")
            save_world()
            save_players()
            save_blacklist()
            with open("permission.json", "w") as f:
                json.dump(permissions, f, indent=4)
            print("[SERVER] All data saved. Goodbye!")
            os._exit(0)

        else:
            return f"Command '{cmd}' is not implemented yet."

    except Exception as e:
        return f"Error executing command '{cmd}': {e}"

# =========================
# CLIENT THREAD
# =========================

def client_thread(client, addr):
    pid = None
    is_refresh = True  # Assume it's a refresh until proven otherwise
    try:
        # Receive player ID from client
        msg = recv_msg(client)
        if not msg or msg.get("type") != "login":
            client.close()
            return
        
        pid = msg["id"]
        password = msg.get("password", "")
        color = msg.get("color", "blue")
        
        # Check password
        server_password = str(config.get("password_server", 0))
        if server_password != "0" and password != server_password:
            send_msg(client, {
                "type": "disconnect",
                "reason": "Incorrect password"
            })
            client.close()
            return
        
        if blacklist.get(pid) == "banned":
            send_msg(client, {
                "type": "disconnect",
                "reason": "You are banned from this server"
            })
            client.close()
            return

        with lock:
            clients[pid] = client
            
            # Initialize player data if not exists
            if pid not in player_data:
                player_data[pid] = {
                    "x": 10,
                    "y": 3,
                    "hotbar": [{"block": "stone", "count": 10}] + [None] * 6,
                    "inventory": [None] * 21,  # Initialize empty inventory!
                    "color": color
                }
                save_players()
            else:
                # Update color
                player_data[pid]["color"] = color
                # Ensure inventory exists for old players
                if "inventory" not in player_data[pid]:
                    player_data[pid]["inventory"] = [None] * 21
                save_players()
            
            player_positions[pid] = (player_data[pid]["x"], player_data[pid]["y"])

        # Send welcome packet
        try:
            send_msg(client, {
                "type": "welcome",
                "id": pid,
                "motd": config["server-motd"],
                "server": config["server-name"],
                "world": world,
                "x": player_data[pid]["x"],
                "y": player_data[pid]["y"],
                "hotbar": player_data[pid]["hotbar"],
                "inventory": player_data[pid].get("inventory", [None] * 21),  # Send inventory!
                "level": get_level(pid),
                "color": player_data[pid].get("color", "blue"),
                "max_players": config["max_players"],
                "current_players": len(clients)
            })
        except:
            with lock:
                clients.pop(pid, None)
                player_positions.pop(pid, None)
            client.close()
            return

        # Send all other players' positions and colors
        with lock:
            for other_pid, pos in player_positions.items():
                if other_pid != pid:
                    try:
                        send_msg(client, {
                            "type": "player_join",
                            "id": other_pid,
                            "x": pos[0],
                            "y": pos[1],
                            "color": player_data[other_pid].get("color", "blue")
                        })
                    except:
                        pass

        # Broadcast new player joined
        broadcast({
            "type": "player_join",
            "id": pid,
            "x": player_positions[pid][0],
            "y": player_positions[pid][1],
            "color": player_data[pid].get("color", "blue")
        }, exclude=pid)

        while True:
            try:
                msg = recv_msg(client)
                if not msg:
                    break

                # If we receive any message, it's not just a refresh
                if is_refresh:
                    is_refresh = False
                    print(f"[SERVER] Player {pid} connected from {addr}")

                if msg["type"] == "chat":
                    if blacklist.get(pid) == "muted":
                        send_msg(client, {
                            "type": "chat",
                            "from": "SERVER",
                            "level": 999,
                            "message": "You are muted."
                        })
                    else:
                        message_text = msg["message"]
                        if message_text.startswith("/"):
                            # Handle command
                            result = handle_command(pid, message_text)
                            if result:
                                send_msg(client, {
                                    "type": "chat",
                                    "from": "SERVER",
                                    "level": 999,
                                    "message": result
                                })
                        else:
                            # Broadcast chat message
                            broadcast({
                                "type": "chat",
                                "from": pid,
                                "level": get_level(pid),
                                "message": message_text
                            })

                elif msg["type"] == "move":
                    x, y = msg["x"], msg["y"]
                    with lock:
                        player_positions[pid] = (x, y)
                        player_data[pid]["x"] = x
                        player_data[pid]["y"] = y
                    
                    # Broadcast position update
                    broadcast({
                        "type": "player_move",
                        "id": pid,
                        "x": x,
                        "y": y
                    }, exclude=pid)

                elif msg["type"] == "update_color":
                    color = msg.get("color", "blue")
                    player_data[pid]["color"] = color
                    save_players()
                    
                    # Broadcast color update to all players
                    broadcast({
                        "type": "player_color",
                        "id": pid,
                        "color": color
                    }, exclude=pid)
                    
                    # Confirm to sender
                    send_msg(client, {
                        "type": "color_updated",
                        "color": color
                    })

                elif msg["type"] == "break_block":
                    x, y = msg["x"], msg["y"]
                    if 0 <= y < len(world) and 0 <= x < len(world[0]):
                        broken_block = world[y][x]
                        # Cannot break bedrock or air
                        if broken_block != "air" and broken_block != "bedrock":
                            world[y][x] = "air"
                            save_world()
                            
                            # Add to player's hotbar (with 64 stack limit)
                            hotbar = player_data[pid]["hotbar"]
                            inventory = player_data[pid].get("inventory", [None] * 21)
                            added = False
                            
                            # Try to stack in existing hotbar slot (max 64)
                            for slot in hotbar:
                                if slot and slot["block"] == broken_block and slot["count"] < 64:
                                    slot["count"] += 1
                                    added = True
                                    break
                            
                            # Try to stack in existing inventory slot (max 64)
                            if not added:
                                for slot in inventory:
                                    if slot and slot["block"] == broken_block and slot["count"] < 64:
                                        slot["count"] += 1
                                        added = True
                                        break
                            
                            # Try empty hotbar slot
                            if not added:
                                for i, slot in enumerate(hotbar):
                                    if slot is None:
                                        hotbar[i] = {"block": broken_block, "count": 1}
                                        added = True
                                        break
                            
                            # Try empty inventory slot
                            if not added:
                                for i, slot in enumerate(inventory):
                                    if slot is None:
                                        inventory[i] = {"block": broken_block, "count": 1}
                                        added = True
                                        break
                            
                            player_data[pid]["inventory"] = inventory
                            save_players()
                            
                            # Send updated hotbar AND inventory to player
                            send_msg(client, {
                                "type": "hotbar_update",
                                "hotbar": hotbar
                            })
                            send_msg(client, {
                                "type": "inventory_update",
                                "inventory": inventory
                            })
                            
                            # Broadcast block update
                            broadcast({
                                "type": "update_block",
                                "x": x,
                                "y": y,
                                "block": "air"
                            })

                elif msg["type"] == "place_block":
                    x, y = msg["x"], msg["y"]
                    slot_index = msg["slot"]
                    
                    if 0 <= y < len(world) and 0 <= x < len(world[0]):
                        if world[y][x] in ["air", "ladder"]:
                            hotbar = player_data[pid]["hotbar"]
                            if 0 <= slot_index < len(hotbar) and hotbar[slot_index]:
                                block_type = hotbar[slot_index]["block"]
                                hotbar[slot_index]["count"] -= 1
                                
                                if hotbar[slot_index]["count"] <= 0:
                                    hotbar[slot_index] = None
                                
                                world[y][x] = block_type
                                save_world()
                                save_players()
                                
                                # Send updated hotbar to player
                                send_msg(client, {
                                    "type": "hotbar_update",
                                    "hotbar": hotbar
                                })
                                
                                # Broadcast block update
                                broadcast({
                                    "type": "update_block",
                                    "x": x,
                                    "y": y,
                                    "block": block_type
                                })
                
                elif msg["type"] == "sync_inventory":
                    # Client is syncing inventory after drag&drop
                    hotbar = msg.get("hotbar", [None] * 7)
                    inventory = msg.get("inventory", [None] * 21)
                    
                    player_data[pid]["hotbar"] = hotbar
                    player_data[pid]["inventory"] = inventory
                    save_players()

            except:
                break

    except Exception as e:
        print(f"[SERVER] Error with client: {e}")
    finally:
        if pid:
            with lock:
                clients.pop(pid, None)
                player_positions.pop(pid, None)
            
            # Only log disconnect and broadcast if they were actually playing
            if not is_refresh:
                # Broadcast player left
                broadcast({
                    "type": "player_leave",
                    "id": pid
                })
                
                print(f"[SERVER] Player {pid} disconnected")
        
        client.close()

# =========================
# CONSOLE THREAD
# =========================

def console():
    """Console input thread - works with Pterodactyl when started with python -u"""
    import sys
    
    console_mode = config.get("console_mode", "interactive")
    
    print("=" * 60, flush=True)
    print(f"[SERVER] Console mode: {console_mode}", flush=True)
    
    if console_mode == "pterodactyl":
        print("[SERVER] Pterodactyl Console Active", flush=True)
        print("[SERVER] ", flush=True)
        print("[SERVER] IMPORTANT: Start server with 'python3 -u server.py'", flush=True)
        print("[SERVER] The -u flag enables unbuffered I/O", flush=True)
        print("[SERVER] ", flush=True)
        print("[SERVER] Type your commands below:", flush=True)
        print("[SERVER] Example: /help", flush=True)
        print("=" * 60, flush=True)
        
        while True:
            try:
                line = sys.stdin.readline()
                
                if line:
                    cmd = line.strip()
                    
                    if cmd:
                        # Echo what we received
                        print(f"[CONSOLE] >> {cmd}", flush=True)
                        
                        if cmd.startswith("/"):
                            result = handle_command("CONSOLE", cmd)
                            if result:
                                print(f"[SERVER] {result}", flush=True)
                        else:
                            print(f"[SERVER] Commands must start with / ", flush=True)
                            print(f"[SERVER] Try: /help", flush=True)
                            
            except KeyboardInterrupt:
                print("[SERVER] Shutting down...", flush=True)
                break
            except Exception as e:
                print(f"[SERVER] Console error: {e}", flush=True)
                time.sleep(0.1)
    else:
        # Interactive mode
        print("[SERVER] Interactive console ready", flush=True)
        print("=" * 60, flush=True)
        
        while True:
            try:
                cmd = input(">>> ")
                if cmd.startswith("/"):
                    result = handle_command("CONSOLE", cmd)
                    if result:
                        print(f"[SERVER] {result}")
            except EOFError:
                print("[SERVER] EOF - switching to non-interactive...", flush=True)
                while True:
                    try:
                        line = sys.stdin.readline()
                        if line:
                            cmd = line.strip()
                            if cmd:
                                print(f"[CONSOLE] >> {cmd}", flush=True)
                                if cmd.startswith("/"):
                                    result = handle_command("CONSOLE", cmd)
                                    if result:
                                        print(f"[SERVER] {result}", flush=True)
                    except:
                        time.sleep(1)
            except Exception as e:
                print(f"[SERVER] Console error: {e}")
                time.sleep(1)

# =========================
# START SERVER
# =========================

server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind((config["host"], config["port"]))
server.listen(config["max_players"])

print(f"[SERVER] {config['server-name']} started on {config['host']}:{config['port']}")

threading.Thread(target=console, daemon=True).start()

while True:
    c, a = server.accept()
    threading.Thread(target=client_thread, args=(c, a), daemon=True).start()
