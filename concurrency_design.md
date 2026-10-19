# Server Concurrency & Thread-Safety Design

## 1. Multi-Threading

One worker thread per client.

- **main thread**: the main thread will be the One who has the listening socket and will call `Accept()` in an infinite loop and never block on the client i/o.
- **worker thread**: each worker thread will have a socket that was created from accepting a new connection with `Accept()` and will always wait/block on `recv()`.

With 2 player connections, there would only need to be 1 listener and 2 worker threads to handle each client simultaneously while the server does not block waiting for input from either client.

## 2. Connection Lifecycle

- **Listen:** create TCP socket, set `SO_REUSEADDR`, `bind()`, `listen()`, enter the `Accept()` loop.
- **Accept + register:** `Accept()` returns a client socket. Assign a role (first = `X`, second = `O`) and add it to the client list. Spawn a worker thread. After both join, broadcast `GAME_START`.
- **Game loop:** the worker reads bytes, buffers them, splits on `\n`, and handles each message. A `MOVE` runs through the critical section below.
- **Teardown:** on EOF (`b""`), a socket error, or a `DISCONNECT`, remove the client, forfeit if a game is active, close the socket, and end the thread.

## 3. Thread Safety

Shared state touched by both workers:

- `board` (3x3 grid)
- `active_player` (whose turn)
- `scores`
- client list

All of it is guarded by one `threading.Lock` (`state_lock`). A worker must hold the lock to read or change shared state, so two moves can never run at the same time.

### Critical section

```
with state_lock:                         # acquire
    if sender != active_player:          -> ERROR OUT_OF_TURN
    elif out of bounds:                  -> ERROR OUT_OF_BOUNDS
    elif cell not empty:                 -> ERROR CELL_TAKEN
    else:
        board[row][col] = mark           # mutate
        result = check win / draw
        if game continues:
            switch active_player         # switch turn
        else:
            scores[winner] += 1
        broadcast STATE_UPDATE or GAME_OVER to both clients
# released
```

Getting the lock for the whole operation eliminates race conditions. Only one thread can be inside the critical section at a time, so the two moves run one after another instead of at once: the first move fully completes and switches the turn before the second can touch the board. This also means the broadcasts to the two clients never overlap.

## 4. Score Sync

- `scores` lives in server memory, guarded by `state_lock`.
- The winner's score is updated inside the same critical section as the move.
- `GAME_OVER` sends the same result and scores to both clients, so both stay in sync.