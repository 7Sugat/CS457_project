# Tic-Tac-Toe

Two-player Tic-Tac-Toe over TCP sockets. 

## Files

| File | Purpose |
|---|---|
| `server.py` | Game server: one thread per client, FSM, move validation |
| `client.py` | Game client: DNS lookup, board display, keyboard input |
| `logs/` | Sprint 3 test run: server, two clients, DNS failure |
| `*.md` | Sprint 1 and 2 design documents |

## Run

Open three terminals:

```bash
python3 server.py -p 8080
python3 client.py -i localhost -p 8080 -n Name
python3 client.py -i localhost -p 8080 -n Name

`-h` shows help. Ports must be 1024 to 65535.

## Play

Type a letter to claim a slot:

```
   a | b | c
  ---+---+---
   d | e | f
  ---+---+---
   g | h | i
```

`q` quits. After a game, `r` requests a rematch (30 seconds).

## Error handling

- **DNS failure:** clear error message, exit code 1, no traceback.
- **Invalid move:** server sends `OUT_OF_TURN`, `OUT_OF_BOUNDS`, or `CELL_TAKEN`; the turn does not change.
- **Bad message:** server sends `MALFORMED` and keeps running.
- **Disconnect mid-game:** the other player wins by forfeit.