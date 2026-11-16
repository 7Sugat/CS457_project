#!/usr/bin/env python3
"""
Tic-Tac-Toe client.

Usage:
    python3 client.py -i localhost -p 8080
    python3 client.py -i server.yourlastname.edu -p 8080 -n Name
"""

import argparse
import json
import socket
import sys
import threading
import time

# Protocol constants (must match server.py)
ALL_MSG_TYPES = {
    "CONNECT", "LOBBY_WAIT", "GAME_START", "MOVE",
    "STATE_UPDATE", "ERROR", "DISCONNECT", "GAME_OVER",
}
REQUIRED_FIELDS = ("msg_type", "player_id", "payload", "timestamp")
RECV_SIZE = 1024
MAX_NAME_LEN = 20
CONNECT_TIMEOUT = 10        # seconds
MIN_PORT, MAX_PORT = 1024, 65535

# Client phases: decide how keyboard input is interpreted
LOBBY = "LOBBY"
PLAYING = "PLAYING"
FINISHED = "FINISHED"       # after GAME_OVER (rematch prompt)

# Slot letters in row-major order: a b c / d e f / g h i
SLOT_LETTERS = "abcdefghi"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def make_message(msg_type, player_id, payload=None):
    """Build a message with the 4 required fields."""
    return {
        "msg_type": msg_type,
        "player_id": player_id,
        "payload": payload if payload is not None else {},
        "timestamp": int(time.time()),
    }


def encode_message(message):
    """Serialize to compact JSON + UTF-8 + one trailing newline."""
    return (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")


def parse_message(line):
    """Decode one line. Returns the message dict, or None if invalid."""
    try:
        message = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    if not isinstance(message, dict):
        return None
    if any(field not in message for field in REQUIRED_FIELDS):
        return None
    if not isinstance(message["msg_type"], str):
        return None
    if message["msg_type"] not in ALL_MSG_TYPES:
        return None
    if not isinstance(message["payload"], dict):
        return None
    return message


def game_finished(board):
    """True if the board has three in a row or no empty cells."""
    lines = [row for row in board]
    lines += [[board[r][c] for r in range(3)] for c in range(3)]
    lines += [[board[i][i] for i in range(3)],
              [board[i][2 - i] for i in range(3)]]
    has_winner = any(line[0] and line.count(line[0]) == 3 for line in lines)
    return has_winner or all(cell for row in board for cell in row)


def render_board(board, side_lines=()):
    """Return the board with empty slots shown as letters a-i.

    side_lines are printed to the right of the board, one per row.
    """
    lines = []
    for r, row in enumerate(board):
        cells = [cell if cell else SLOT_LETTERS[r * 3 + c]
                 for c, cell in enumerate(row)]
        lines.append(f"   {cells[0]} | {cells[1]} | {cells[2]}")
        if r < 2:
            lines.append("  ---+---+---")
    side = list(side_lines) + [""] * (len(lines) - len(side_lines))
    return "\n".join(f"{line:<16}{info}".rstrip()
                     for line, info in zip(lines, side))


def render_scores(scores):
    """Return scores as 'Alice: 1 | Bob: 0'."""
    return " | ".join(f"{name}: {wins}" for name, wins in scores.items())


def parse_move(text):
    """Convert 'a'-'i' or 'row col' into (row, col). None if invalid.

    Letters map row by row: a=(0,0), b=(0,1) ... i=(2,2).
    Numbers are not range-checked here; the server does that.
    """
    text = text.lower()
    if len(text) == 1 and text in SLOT_LETTERS:
        return divmod(SLOT_LETTERS.index(text), 3)
    parts = text.replace(",", " ").split()
    if len(parts) == 2 and all(part.lstrip("-").isdigit() for part in parts):
        return int(parts[0]), int(parts[1])
    return None


# ---------------------------------------------------------------------------
# Game client
# ---------------------------------------------------------------------------
class GameClient:
    """Receiver thread reads the socket; main thread reads the keyboard.

    Separate threads let server messages appear while input() is waiting.
    """

    def __init__(self, sock, name):
        self.sock = sock
        self.name = name
        self.mark = None
        self.opponent = None
        self.opponent_mark = None
        self.phase = LOBBY
        self.my_turn = False
        self.scores = {}                    # name -> wins, counted locally
        self.ended = threading.Event()      # set when the session is over
        self.lock = threading.Lock()        # guards state and printing
        self.send_lock = threading.Lock()   # one sendall() at a time

    def send(self, msg_type, payload=None):
        """Send one message. Returns False if the socket is dead."""
        data = encode_message(make_message(msg_type, self.name, payload))
        with self.send_lock:
            try:
                self.sock.sendall(data)
                return True
            except OSError:
                return False

    # ----- main thread: keyboard -----------------------------------------
    def run(self):
        """Start the receiver, send CONNECT, then read keyboard input."""
        print(f"Connected as {self.name}. Pick a slot with a letter "
              f"(a-i). Type 'q' to quit.\n")
        threading.Thread(target=self.receive_loop, daemon=True).start()
        self.send("CONNECT")

        try:
            while not self.ended.is_set():
                try:
                    line = input()
                except EOFError:
                    break
                if self.ended.is_set():
                    break
                self.handle_input(line.strip())
        except KeyboardInterrupt:
            print()
        finally:
            if not self.ended.is_set():
                self.send("DISCONNECT")
                self.ended.set()
            try:
                self.sock.close()
            except OSError:
                pass
            print("Goodbye.")

    def handle_input(self, text):
        """Convert one line of input into MOVE, CONNECT, or DISCONNECT."""
        if not text:
            return
        if text.lower() in ("q", "quit", "exit"):
            self.send("DISCONNECT")
            self.ended.set()
            return

        with self.lock:
            phase = self.phase

        if phase == FINISHED:
            if text.lower() in ("r", "rematch"):
                print("Rematch requested...")
                self.send("CONNECT")
            else:
                print("Type 'r' for a rematch or 'q' to quit.")
        elif phase == PLAYING:
            move = parse_move(text)
            if move is None:
                print("Enter a slot letter a-i (or row col, like: 1 2).")
                return
            # Turn, bounds, and taken cells are validated by the server.
            self.send("MOVE", {"row": move[0], "col": move[1]})
        else:
            print("The game has not started yet. Please wait...")

    # ----- receiver thread: network --------------------------------------
    def receive_loop(self):
        """Receive bytes, split on newline, handle each message."""
        buffer = b""
        try:
            while True:
                data = self.sock.recv(RECV_SIZE)
                if not data:                    # EOF: server closed
                    break
                buffer += data
                # A recv() can hold several messages or part of one.
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if line.strip():
                        self.handle_message(line)
        except OSError:
            pass    # socket closed locally or reset

        if not self.ended.is_set():
            self.ended.set()
            with self.lock:
                print("\n[INFO] Server closed the connection. "
                      "Press Enter to exit.")

    def handle_message(self, line):
        """Route one server message to its handler."""
        message = parse_message(line)
        if message is None:
            with self.lock:
                print("[WARN] Ignored a malformed message from the server.")
            return

        handlers = {
            "LOBBY_WAIT": self.on_lobby_wait,
            "GAME_START": self.on_game_start,
            "STATE_UPDATE": self.on_state_update,
            "ERROR": self.on_error,
            "GAME_OVER": self.on_game_over,
        }
        handler = handlers.get(message["msg_type"])
        if handler:
            with self.lock:
                handler(message["payload"])

    # ----- message handlers (called with self.lock held) -----------------
    def side_panel(self):
        """Info lines shown to the right of the board."""
        me = self.scores.get(self.name, 0)
        them = self.scores.get(self.opponent, 0)
        return [
            f"You:      {self.name} ({self.mark})",
            f"Opponent: {self.opponent} ({self.opponent_mark})",
            f"Score:    {me} - {them}",
            "",
            "Type a letter to play.",
        ]

    def show_turn_status(self, active_player):
        """Print 'Your Turn' or 'Waiting for <opponent>...'."""
        self.my_turn = active_player == self.name
        if self.my_turn:
            print(f">>> Your Turn ({self.mark}). Pick a slot (a-i):")
        elif active_player:
            print(f"Waiting for {active_player}...")

    def on_lobby_wait(self, payload):
        """LOBBY_WAIT: waiting for the other player."""
        print(f"[LOBBY] {payload.get('message', 'Waiting for player 2...')}")

    def on_game_start(self, payload):
        """GAME_START: record our mark and show the empty board."""
        p1, p2 = payload.get("player1"), payload.get("player2")
        mark1 = payload.get("player1_mark", "X")
        mark2 = payload.get("player2_mark", "O")
        self.mark = mark1 if self.name == p1 else mark2
        self.opponent = p2 if self.name == p1 else p1
        self.opponent_mark = mark2 if self.name == p1 else mark1
        self.phase = PLAYING
        for player in (p1, p2):
            self.scores.setdefault(player, 0)

        print("\n" + "=" * 40)
        print(f" GAME START: {p1} ({mark1}) vs {p2} ({mark2})")
        print(f" You are {self.mark}. X goes first.")
        print("=" * 40)
        print(render_board([["", "", ""] for _ in range(3)],
                           self.side_panel()))
        self.show_turn_status(payload.get("active_player"))

    def on_state_update(self, payload):
        """STATE_UPDATE: redraw the board and show whose turn it is."""
        board = payload.get("board", [])
        print()
        print(render_board(board, self.side_panel()))
        if game_finished(board):
            self.my_turn = False     # GAME_OVER follows; no prompt
        else:
            self.show_turn_status(payload.get("active_player"))

    def on_error(self, payload):
        """ERROR: show the reason; re-prompt if it is still our turn."""
        print(f"[ERROR] {payload.get('code')}: {payload.get('message')}")
        if self.phase == PLAYING and self.my_turn:
            print(f">>> Still your turn ({self.mark}). "
                  f"Pick a slot (a-i):")

    def on_game_over(self, payload):
        """GAME_OVER: show result and scores, offer a rematch."""
        result = payload.get("result")
        winner = payload.get("winner")
        self.phase = FINISHED
        self.my_turn = False

        print("\n" + "=" * 40)
        if result == "DRAW":
            print(" GAME OVER: Draw - the board is full.")
        elif result == "FORFEIT":
            print(" GAME OVER: Opponent disconnected. You win by forfeit!")
        elif winner == self.name:
            print(" GAME OVER: You win! Three in a row.")
        else:
            print(f" GAME OVER: {winner} wins. Three in a row.")
        if winner in self.scores:
            self.scores[winner] += 1
        print(f" Score: {render_scores(self.scores)}")
        print("=" * 40)

        # After a forfeit the server ends the session, so no rematch.
        if result != "FORFEIT":
            print("Type 'r' for a rematch or 'q' to quit (30 seconds).")


# ---------------------------------------------------------------------------
# CLI and connection setup
# ---------------------------------------------------------------------------
def port_number(text):
    """argparse type: integer port in 1024-65535."""
    try:
        port = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{text}' is not a number")
    if not MIN_PORT <= port <= MAX_PORT:
        raise argparse.ArgumentTypeError(
            f"{port} is out of range ({MIN_PORT}-{MAX_PORT})")
    return port


def parse_args():
    """Parse -h, -i, -p, and optional -n."""
    parser = argparse.ArgumentParser(
        description="Tic-Tac-Toe game client.",
        epilog="example: python3 client.py -i server.smith.edu -p 8080",
    )
    parser.add_argument(
        "-i", "--hostname", required=True,
        help="server DNS hostname or IP address")
    parser.add_argument(
        "-p", "--port", type=port_number, required=True,
        help=f"server port ({MIN_PORT}-{MAX_PORT})")
    parser.add_argument(
        "-n", "--name",
        help="your player name (asked for if not given)")
    return parser.parse_args()


def resolve_hostname(hostname):
    """Resolve hostname via DNS. Exits with status 1 on failure."""
    try:
        return socket.gethostbyname(hostname)
    except (socket.gaierror, UnicodeError):
        # UnicodeError: raised instead of gaierror for some invalid
        # names, e.g. a label over 63 characters.
        print(f"[ERROR] Unable to resolve DNS hostname '{hostname}'.")
        print("Please check your Cisco router DNS configuration "
              "('ip dns server' / 'ip host') or verify domain spelling.")
        sys.exit(1)


def connect_to_server(hostname, server_ip, port):
    """Open the TCP connection. Exits with status 1 on failure."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(CONNECT_TIMEOUT)
    try:
        sock.connect((server_ip, port))
    except ConnectionRefusedError:
        print(f"[ERROR] Connection refused by {hostname} ({server_ip}) "
              f"on port {port}. Is the server running?")
        sys.exit(1)
    except socket.timeout:
        print(f"[ERROR] Timed out connecting to {hostname} ({server_ip}) "
              f"on port {port}. Check routing and firewall settings.")
        sys.exit(1)
    except OSError as exc:
        print(f"[ERROR] Could not connect to {hostname} ({server_ip}) "
              f"on port {port}: {exc.strerror or exc}")
        sys.exit(1)
    sock.settimeout(None)   # blocking mode for the game
    return sock


def ask_name():
    """Prompt for a player name when -n is not given."""
    name = ""
    while not name:
        name = input("Enter your player name: ").strip()
    return name


def main():
    """Entry point."""
    args = parse_args()
    try:
        server_ip = resolve_hostname(args.hostname)
        print(f"Resolved {args.hostname} -> {server_ip}")
        name = (args.name or ask_name()).strip()[:MAX_NAME_LEN]
        sock = connect_to_server(args.hostname, server_ip, args.port)
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        sys.exit(1)
    GameClient(sock, name).run()


if __name__ == "__main__":
    main()
