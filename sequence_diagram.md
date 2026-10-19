# State Synchronization & Mermaid Sequence Diagram

Round trip when Player 1 makes a move: the client sends `MOVE`, the server validates turn and legality under `state_lock`, mutates the board, and broadcasts the new state to **both** clients. The `alt`/`else` branches cover valid moves, game-ending moves with score broadcast, and invalid/out-of-turn moves returning `ERROR`.

```mermaid
sequenceDiagram
    autonumber
    actor C1 as Client 1 (Player X)
    participant S as Game Server
    actor C2 as Client 2 (Player O)

    C1->>S: MOVE (row, col)
    Note over S: Acquire state_lock<br/>Validate: is it C1's turn?<br/>Is cell in bounds and empty?

    alt Valid move
        Note over S: Apply move, switch turn,<br/>check win or draw
        alt Game continues
            S-->>C1: STATE_UPDATE (board, turn O)
            S-->>C2: STATE_UPDATE (board, turn O)
        else Win or draw
            Note over S: Update round scores
            S-->>C1: GAME_OVER (result, scores)
            S-->>C2: GAME_OVER (result, scores)
        end
        Note over S: Release state_lock
    else Invalid or out of turn
        S-->>C1: ERROR (code)
        Note over S: Release state_lock
    end
```

## Flow

1. Client 1 sends `MOVE (row, col)`.
2. Server acquires `state_lock` and validates: is it Client 1's turn, and is the cell in bounds and empty?
3. **Valid, game continues:** apply the move, switch the turn, broadcast `STATE_UPDATE` to both clients.
4. **Valid, win or draw:** update scores, broadcast `GAME_OVER` (result, scores) to both clients.
5. **Invalid or out of turn:** send `ERROR` to the sender only; board and turn stay unchanged.
6. `state_lock` is released at the end, so the next move is handled cleanly.
