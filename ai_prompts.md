## 1. Protocol / Message Handling
- Write Python code for my 2-player TCP Tic-Tac-Toe game.
- Use JSON messages with `\n` as the message delimiter.
- Every message must contain: `msg_type`, `player_id`, `payload`, `timestamp`.
- Only use these message types:
  `CONNECT`, `LOBBY_WAIT`, `GAME_START`, `MOVE`,
  `STATE_UPDATE`, `ERROR`, `DISCONNECT`, `GAME_OVER`.
- Do NOT create extra fields or message types.
- Do NOT assume one `recv()` call equals one complete message.
- Buffer incoming data and split complete messages using `\n`.
- Identify players by their socket, not by the `player_id` in the message.
- Test: `feed(b'{...}\n{...}')` returns 2 messages; a split message returns none until its `\n` arrives.

## 2. Serialization
- Implement `encode_message(msg_type, player_id, payload, timestamp) -> bytes`.
- Build a dict with exactly those 4 keys, `json.dumps` it (no indent), UTF-8 encode, append one `\n`.
- Raise `ValueError` if `msg_type` is not one of the 8 allowed types.
- Test: `encode_message("MOVE","JohnTTT",{"row":0,"col":2},1727000005)` ends with `...,"timestamp":1727000005}\n`.

## 3. MOVE Validation
- Implement move validation for a 3x3 Tic-Tac-Toe board.
- Check errors in this order:
  1. Wrong player turn -> `OUT_OF_TURN`
  2. Row/column outside `0-2` -> `OUT_OF_BOUNDS`
  3. Cell already used -> `CELL_TAKEN`
- Return `None` if the move is valid.
- Do NOT modify the board inside the validation function.
- Invalid moves must send `ERROR` and keep the same active player.
- Test: an out-of-turn move on a taken cell returns `OUT_OF_TURN`.

## 4. Win/Draw Check
- Implement `check_result(board)` returning `("WIN", mark)`, `("DRAW", None)`, or `("CONTINUE", None)`.
- Check all 8 winning lines (3 rows, 3 columns, 2 diagonals) explicitly, standard library only.
- Return `DRAW` only when every cell is filled and there is no winner.

## 5. Game State Machine
- Implement the server using only these states:
  `INIT`, `WAITING_FOR_PLAYERS`, `GAME_START`, `PLAYER_TURN`,
  `EVALUATE_MOVE`, `CHECK_WIN_DRAW`, `GAME_OVER`,
  `REMATCH`, `CLIENT_DISCONNECTED`, `CLEANUP`.
- Player 1 uses `X`, Player 2 uses `O`, and `X` moves first.
- Valid MOVE -> update board -> send `STATE_UPDATE` -> check win/draw.
- Invalid MOVE -> send `ERROR` -> return to `PLAYER_TURN`.
- After `GAME_OVER`, if both players stay connected, go to `REMATCH`. A `CONNECT` from a player means accept, and a `DISCONNECT` means decline. If both accept within 30 seconds, reset the board and send `GAME_START`. Otherwise go to `CLEANUP`.
- Do NOT add extra states or transitions.

## 6. TCP Disconnect Handling
- If `recv()` returns `b""`, treat it as a client disconnect.
- Stop reading from the socket after EOF.
- Handle:
  `ConnectionResetError`,
  `BrokenPipeError`,
  `ConnectionAbortedError`.
- On disconnect, move to `CLIENT_DISCONNECTED` and clean up.
- Do NOT retry a dead socket or ignore socket errors.

## 7. Code Requirements
- Use Python sockets and standard libraries only.
- Keep the code consistent with my protocol blueprint and FSM.
- Reject malformed JSON or unknown `msg_type` with an `ERROR` (`MALFORMED`); never crash.
- Do NOT replace my protocol with other code.
- Do NOT add features I did not ask for.
- If something is unclear, ask instead of guessing.

## 8. Testing
- Make tests for each function after writing it.
- Test:
  1. message framing and buffering
  2. JSON encode/decode
  3. all 3 MOVE errors in the correct order
  4. win, draw, and continue results
  5. disconnects using b"" and socket errors
  6. rematch: both CONNECT starts a new game 
              DISCONNECT or timeout goes to cleanup
- Run the tests and show pass/fail.
- If a test fails, fix the code, not my protocol or FSM.
- Do NOT add fields, message types, or states that are not in my design.

