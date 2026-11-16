#!/usr/bin/env python3
"""
Tic-Tac-Toe server.

Usage: python3 server.py -p 8080
"""

import argparse
import json
import socket
import sys
import threading
import time

# Protocol constants
ALL_MSG_TYPES = {
    "CONNECT", "LOBBY_WAIT", "GAME_START", "MOVE",
    "STATE_UPDATE", "ERROR", "DISCONNECT", "GAME_OVER",
}
CLIENT_MSG_TYPES = {"CONNECT", "MOVE", "DISCONNECT"}
REQUIRED_FIELDS = ("msg_type", "player_id", "payload", "timestamp")
SERVER_ID = "SERVER"

RECV_SIZE = 1024
MAX_BUFFER = 64 * 1024      # max bytes held without a newline
MAX_NAME_LEN = 20
REMATCH_TIMEOUT = 30        # seconds
MAX_PLAYERS = 2
MIN_PORT, MAX_PORT = 1024, 65535

# FSM states
INIT = "INIT"
WAITING_FOR_PLAYERS = "WAITING_FOR_PLAYERS"
GAME_START = "GAME_START"
PLAYER_TURN = "PLAYER_TURN"
EVALUATE_MOVE = "EVALUATE_MOVE"
CHECK_WIN_DRAW = "CHECK_WIN_DRAW"
GAME_OVER = "GAME_OVER"
REMATCH = "REMATCH"
CLIENT_DISCONNECTED = "CLIENT_DISCONNECTED"
CLEANUP = "CLEANUP"

# The 8 winning lines: 3 rows, 3 columns, 2 diagonals
WIN_LINES = (
    [((r, 0), (r, 1), (r, 2)) for r in range(3)]
    + [((0, c), (1, c), (2, c)) for c in range(3)]
    + [((0, 0), (1, 1), (2, 2)), ((0, 2), (1, 1), (2, 0))]
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def log(text):
    """Print a timestamped log line."""
    # A single write keeps lines from different threads separate.
    sys.stdout.write(f"[{time.strftime('%H:%M:%S')}] {text}\n")
    sys.stdout.flush()


def make_message(msg_type, payload, player_id=SERVER_ID):
    """Build a message with the 4 required fields."""
    return {
        "msg_type": msg_type,
        "player_id": player_id,
        "payload": payload,
        "timestamp": int(time.time()),
    }


def encode_message(message):
    """Serialize to compact JSON + UTF-8 + one trailing newline."""
    # json.dumps escapes newlines inside strings, so the only raw "\n"
    # is the delimiter added here.
    return (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")


def parse_message(line):
    """Decode one line. Returns (message, None) or (None, error reason)."""
    try:
        message = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        # ValueError covers JSONDecodeError and oversized numbers;
        # RecursionError covers extremely deep nesting.
        return None, "Message is not valid UTF-8 JSON"

    if not isinstance(message, dict):
        return None, "Message must be a JSON object"
    missing = [field for field in REQUIRED_FIELDS if field not in message]
    if missing:
        return None, f"Missing field(s): {', '.join(missing)}"
    if not isinstance(message["msg_type"], str):
        return None, "msg_type must be a string"
    if message["msg_type"] not in ALL_MSG_TYPES:
        return None, f"Unknown msg_type: {message['msg_type']!r}"
    if not isinstance(message["player_id"], str):
        return None, "player_id must be a string"
    if not isinstance(message["payload"], dict):
        return None, "payload must be a JSON object"
    if not is_int(message["timestamp"]):
        return None, "timestamp must be an integer"
    return message, None


def is_int(value):
    """True for ints only (bool is a subclass of int, so exclude it)."""
    return isinstance(value, int) and not isinstance(value, bool)


def new_board():
    """Return an empty 3x3 board."""
    return [["", "", ""] for _ in range(3)]


def find_winner(board):
    """Return "X" or "O" if a line is complete, else None."""
    for line in WIN_LINES:
        marks = {board[r][c] for r, c in line}
        if len(marks) == 1 and "" not in marks:
            return marks.pop()
    return None


def board_is_full(board):
    """True when every cell is filled."""
    return all(cell for row in board for cell in row)


# ---------------------------------------------------------------------------
# Player
# ---------------------------------------------------------------------------
class Player:
    """One client connection. Players are identified by socket, not by
    the player_id they send."""

    def __init__(self, sock, addr):
        self.sock = sock
        self.addr = f"{addr[0]}:{addr[1]}"
        self.name = None
        self.mark = None
        self.registered = False      # True after a valid CONNECT
        self.wants_rematch = False
        # Both workers can send to this socket; sendall() is not
        # thread-safe, so sends are serialized per socket.
        self.send_lock = threading.Lock()

    def send(self, message):
        """Send one message. Returns False if the socket is dead."""
        data = encode_message(message)
        with self.send_lock:
            try:
                self.sock.sendall(data)
                return True
            except OSError:
                # Covers BrokenPipe/ConnectionReset/ConnectionAborted.
                # This player's worker handles the disconnect on recv().
                return False

    def send_error(self, code, text):
        """Send an ERROR message to this player."""
        self.send(make_message("ERROR", {"code": code, "message": text}))

    def close(self):
        """Shut down and close the socket. Safe to call twice."""
        try:
            # shutdown() unblocks this player's worker thread in recv().
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass

    def label(self):
        """Name for log lines."""
        return f"{self.name} ({self.mark})" if self.name else self.addr


# ---------------------------------------------------------------------------
# Game server
# ---------------------------------------------------------------------------
class GameServer:
    """Accept loop, shared game state, and FSM.

    All shared state is guarded by state_lock. Methods ending in _locked
    must be called with state_lock held (the lock is not re-entrant).
    """

    def __init__(self, port):
        self.port = port
        self.state_lock = threading.Lock()
        self.state = INIT
        self.players = []           # index 0 = X, index 1 = O
        self.board = new_board()
        self.active_player = None
        self.scores = {}            # name -> wins
        self.game_id = 0            # identifies the current game for timers
        self.rematch_timer = None

    # ----- main thread ---------------------------------------------------
    def serve_forever(self):
        """Bind to 0.0.0.0, then accept clients and spawn a worker each."""
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(("0.0.0.0", self.port))
        except OSError as exc:
            print(f"[ERROR] Cannot bind to 0.0.0.0:{self.port} "
                  f"({exc.strerror}). Is the port already in use?")
            listener.close()
            sys.exit(1)
        listener.listen()
        listener.settimeout(1.0)    # lets Ctrl+C work on Windows

        log(f"Server listening on 0.0.0.0:{self.port} (Ctrl+C to stop)")
        with self.state_lock:
            self.set_state_locked(WAITING_FOR_PLAYERS)

        try:
            while True:
                try:
                    conn, addr = listener.accept()
                except socket.timeout:
                    continue
                conn.settimeout(None)   # worker uses blocking recv()
                threading.Thread(target=self.handle_client,
                                 args=(Player(conn, addr),),
                                 daemon=True).start()
        except KeyboardInterrupt:
            log("Shutting down server.")
        finally:
            with self.state_lock:
                for player in self.players:
                    player.close()
            listener.close()

    # ----- worker thread -------------------------------------------------
    def handle_client(self, player):
        """Receive bytes, split on newline, process each message."""
        log(f"TCP connection from {player.addr}")
        buffer = b""
        graceful = False
        try:
            while True:
                data = player.sock.recv(RECV_SIZE)
                if not data:                    # EOF: client closed
                    log(f"{player.label()} closed the connection (EOF).")
                    return
                buffer += data

                # A recv() can hold several messages or part of one.
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if not line.strip():
                        continue
                    if not self.process_line(player, line):
                        graceful = True         # DISCONNECT or rejected
                        return

                if len(buffer) > MAX_BUFFER:
                    player.send_error("MALFORMED", "Message too long")
                    buffer = b""
        except (ConnectionResetError, BrokenPipeError,
                ConnectionAbortedError) as exc:
            log(f"{player.label()} connection lost ({type(exc).__name__}).")
        except OSError:
            pass    # socket closed by cleanup on another thread
        except Exception as exc:    # safety net: never crash on bad input
            log(f"Unexpected error from {player.label()}: {exc!r}")
        finally:
            self.handle_disconnect(player, graceful)

    def process_line(self, player, line):
        """Handle one message. Returns False to close this connection."""
        message, problem = parse_message(line)
        if problem:
            log(f"MALFORMED from {player.label()}: {problem}")
            player.send_error("MALFORMED", problem)
            return True

        msg_type = message["msg_type"]
        if msg_type not in CLIENT_MSG_TYPES:
            player.send_error("MALFORMED",
                              f"Clients may not send {msg_type}")
            return True

        if msg_type == "DISCONNECT":
            log(f"{player.label()} sent DISCONNECT.")
            return False

        with self.state_lock:
            if msg_type == "CONNECT":
                return self.on_connect_locked(player, message)
            self.on_move_locked(player, message)
            return True

    # ----- FSM (caller holds state_lock) ---------------------------------
    def set_state_locked(self, new_state):
        """Change FSM state and log the transition."""
        if new_state != self.state:
            log(f"FSM: {self.state} -> {new_state}")
            self.state = new_state

    def broadcast_locked(self, message):
        """Send a message to both players."""
        for player in self.players:
            player.send(message)

    def on_connect_locked(self, player, message):
        """CONNECT: join the lobby, or accept a rematch."""
        if player.registered:
            if self.state == REMATCH:
                player.wants_rematch = True
                log(f"{player.label()} accepted the rematch.")
                if all(p.wants_rematch for p in self.players):
                    self.start_game_locked()
                else:
                    player.send(make_message("LOBBY_WAIT", {
                        "message": "Waiting for opponent to accept rematch"}))
            else:
                player.send_error("MALFORMED", "Already connected")
            return True

        if self.state != WAITING_FOR_PLAYERS \
                or len(self.players) >= MAX_PLAYERS:
            log(f"Rejected {player.addr}: game is full.")
            player.send_error("MALFORMED",
                              "Game is full. Try again later.")
            return False

        name = message["player_id"].strip()[:MAX_NAME_LEN]
        if not name or name == SERVER_ID:
            name = f"Player{len(self.players) + 1}"
        if any(p.name == name for p in self.players):
            log(f"Rejected {player.addr}: name {name!r} is taken.")
            player.send_error("MALFORMED",
                              f"Name {name!r} is already in use. "
                              f"Reconnect with a different name.")
            return False

        # Role is assigned on CONNECT (not accept) because the name
        # arrives in CONNECT. First = X, second = O.
        player.name = name
        player.mark = "X" if not self.players else "O"
        player.registered = True
        self.players.append(player)
        self.scores.setdefault(name, 0)
        log(f"{player.label()} joined from {player.addr} "
            f"[{len(self.players)}/{MAX_PLAYERS}]")

        if len(self.players) < MAX_PLAYERS:
            player.send(make_message("LOBBY_WAIT", {
                "message": "Waiting for another player"}))
        else:
            self.start_game_locked()
        return True

    def start_game_locked(self):
        """GAME_START: reset board, X moves first, notify both players."""
        self.cancel_rematch_timer_locked()
        self.game_id += 1
        self.set_state_locked(GAME_START)
        self.board = new_board()
        player_x, player_o = self.players
        self.active_player = player_x
        for player in self.players:
            player.wants_rematch = False

        self.broadcast_locked(make_message("GAME_START", {
            "player1": player_x.name,
            "player2": player_o.name,
            "player1_mark": player_x.mark,
            "player2_mark": player_o.mark,
            "active_player": player_x.name,
        }))
        log(f"Game #{self.game_id}: {player_x.label()} vs "
            f"{player_o.label()}")
        self.set_state_locked(PLAYER_TURN)

    def on_move_locked(self, player, message):
        """MOVE: the critical section from concurrency_design.md.

        Validate (OUT_OF_TURN, OUT_OF_BOUNDS, CELL_TAKEN), update the
        board, check win/draw, switch turn, broadcast.
        """
        if self.state != PLAYER_TURN or not player.registered:
            player.send_error("OUT_OF_TURN", "No game is in progress")
            return

        row = message["payload"].get("row")
        col = message["payload"].get("col")
        if not is_int(row) or not is_int(col):
            player.send_error("MALFORMED",
                              "MOVE payload needs integer row and col")
            return

        self.set_state_locked(EVALUATE_MOVE)
        if player is not self.active_player:
            error = ("OUT_OF_TURN", "It is not your turn")
        elif not (0 <= row <= 2 and 0 <= col <= 2):
            error = ("OUT_OF_BOUNDS", "Row and col must be 0, 1, or 2")
        elif self.board[row][col]:
            error = ("CELL_TAKEN", "That cell is already taken")
        else:
            error = None

        if error:
            log(f"Invalid move by {player.label()} at ({row},{col}): "
                f"{error[0]}")
            player.send_error(*error)
            self.set_state_locked(PLAYER_TURN)   # turn does not change
            return

        self.board[row][col] = player.mark
        log(f"{player.label()} played ({row},{col})")
        self.set_state_locked(CHECK_WIN_DRAW)

        winner_mark = find_winner(self.board)
        game_ended = bool(winner_mark) or board_is_full(self.board)
        self.active_player = self.other_player_locked(player)

        self.broadcast_locked(make_message("STATE_UPDATE", {
            "board": self.board,
            "active_player": self.active_player.name,
        }))

        if winner_mark:
            self.scores[player.name] += 1
            self.end_game_locked("WIN", player.name, "THREE_IN_A_ROW")
        elif game_ended:
            self.end_game_locked("DRAW", None, "BOARD_FULL")
        else:
            self.set_state_locked(PLAYER_TURN)

    def end_game_locked(self, result, winner, reason):
        """GAME_OVER: notify both players, then REMATCH or CLEANUP."""
        self.set_state_locked(GAME_OVER)
        self.active_player = None
        self.broadcast_locked(make_message("GAME_OVER", {
            "result": result,
            "winner": winner,
            "reason": reason,
        }))
        log(f"Game over: {result} ({reason}), winner={winner}, "
            f"scores={self.scores}")

        if len(self.players) == MAX_PLAYERS:
            self.set_state_locked(REMATCH)
            self.rematch_timer = threading.Timer(
                REMATCH_TIMEOUT, self.on_rematch_timeout,
                args=(self.game_id,))
            self.rematch_timer.daemon = True
            self.rematch_timer.start()
            log(f"Waiting {REMATCH_TIMEOUT}s for rematch.")
        else:
            self.cleanup_locked()

    def on_rematch_timeout(self, game_id):
        """Timer thread: end the session if the rematch was not accepted."""
        with self.state_lock:
            # Skip if a new game already started since this timer was set.
            if self.state == REMATCH and self.game_id == game_id:
                log("Rematch timed out.")
                self.cleanup_locked()

    def handle_disconnect(self, player, graceful):
        """Remove a player: forfeit if mid-game, otherwise clean up.

        graceful is True when the client sent DISCONNECT (or was
        rejected), False for EOF or a socket error.
        """
        with self.state_lock:
            if player not in self.players:
                player.close()      # never joined, or already cleaned up
                return

            game_active = self.state == PLAYER_TURN
            declined_rematch = self.state == REMATCH and graceful
            self.players.remove(player)
            log(f"{player.label()} left the game.")

            if declined_rematch:
                # FSM: REMATCH -> CLEANUP on DISCONNECT
                self.cleanup_locked()
            else:
                self.set_state_locked(CLIENT_DISCONNECTED)
                if game_active:
                    opponent = self.players[0]
                    self.scores[opponent.name] += 1
                    self.end_game_locked("FORFEIT", opponent.name,
                                         "OPPONENT_DISCONNECTED")
                else:
                    self.cleanup_locked()
        player.close()

    def cleanup_locked(self):
        """CLEANUP: close all sessions, reset, go back to waiting."""
        self.set_state_locked(CLEANUP)
        self.cancel_rematch_timer_locked()
        for player in self.players:
            player.close()
        self.players = []
        self.board = new_board()
        self.active_player = None
        self.scores = {}
        self.set_state_locked(WAITING_FOR_PLAYERS)
        log("Session cleared. Waiting for players...")

    def cancel_rematch_timer_locked(self):
        """Cancel the pending rematch timer, if any."""
        if self.rematch_timer is not None:
            self.rematch_timer.cancel()
            self.rematch_timer = None

    def other_player_locked(self, player):
        """Return the opponent of the given player."""
        return self.players[1] if player is self.players[0] \
            else self.players[0]


# ---------------------------------------------------------------------------
# CLI
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
    """Parse -h and -p. argparse prints usage and exits on bad input."""
    parser = argparse.ArgumentParser(
        description="Tic-Tac-Toe game server. "
                    "Listens on all interfaces (0.0.0.0).",
        epilog="example: python3 server.py -p 8080",
    )
    parser.add_argument(
        "-p", "--port", type=port_number, required=True,
        help=f"listening port ({MIN_PORT}-{MAX_PORT})")
    return parser.parse_args()


def main():
    """Entry point."""
    args = parse_args()
    GameServer(args.port).serve_forever()


if __name__ == "__main__":
    main()
