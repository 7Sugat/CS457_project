# Application Protocol Blueprint

## 1. Overview

This protocol is for a 2-player Tic-Tac-Toe game using a TCP client/server setup.

The server controls the game and both clients send or receive JSON messages.

## 2. Framing Rule

I chose newline-delimited JSON.

Each JSON message ends with `\n`.

The receiver keeps incoming data in a buffer and splits messages when it finds `\n`.

Example:

```text
{"msg_type":"MOVE","player_id":"JohnTTT","payload":{"row":0,"col":2},"timestamp":1727000005}\n
```

Every message still uses these fields:

- `msg_type`
- `player_id`
- `payload`
- `timestamp`


|Field        |Types    |Rule                                             |
|-------------|---------|-------------------------------------------------|
| `msg_type`  | string  | One of the 8 allowed types                      |
| `player_id` | string  | Player name, or `"SERVER"` for server messages  |
| `payload`   | object  | Contents depend on `msg_type`; `{}` if empty    |
| `timestamp` | integer | Unix time in seconds, set by the sender         |


The server does not trust the `player_id` a client sends. It identifies each player by their socket, so a client cannot send moves as the other player.

Only these message types are allowed:

- `CONNECT`
- `LOBBY_WAIT`
- `GAME_START`
- `MOVE`
- `STATE_UPDATE`
- `ERROR`
- `DISCONNECT`
- `GAME_OVER`



## 3. Message Types

### CONNECT

Direction: Client -> Server

Used when a client first joins the server.

```json
{
  "msg_type": "CONNECT",
  "player_id": "JohnTTT",
  "payload": {},
  "timestamp": 1727000000
}
```

### LOBBY_WAIT

Direction: Server -> Client

Sent to Player 1 while waiting for Player 2.

```json
{
  "msg_type": "LOBBY_WAIT",
  "player_id": "SERVER",
  "payload": {
    "message": "Waiting for another player"
  },
  "timestamp": 1727000001
}
```

### GAME_START

Direction: Server -> Clients

Sent when both players are connected and the game is ready to start.

```json
{
  "msg_type": "GAME_START",
  "player_id": "SERVER",
  "payload": {
    "player1": "JohnTTT",
    "player2": "JaneTTT",
    "player1_mark": "X",
    "player2_mark": "O",
    "active_player": "JohnTTT"
  },
  "timestamp": 1727000002
}
```

### MOVE

Direction: Client -> Server

Sent when the active player chooses a board position.

The payload must contain only `row` and `col`.

```json
{
  "msg_type": "MOVE",
  "player_id": "JohnTTT",
  "payload": {
    "row": 0,
    "col": 2
  },
  "timestamp": 1727000005
}
```

Rules:

- `row` must be an integer from 0 to 2
- `col` must be an integer from 0 to 2
- the player must be the active player
- the selected cell must be empty

Move errors are checked in this order:

1. `OUT_OF_TURN`
2. `OUT_OF_BOUNDS`
3. `CELL_TAKEN`

### STATE_UPDATE

Direction: Server -> Clients

Sent after a valid move.

```json
{
  "msg_type": "STATE_UPDATE",
  "player_id": "SERVER",
  "payload": {
    "board": [
      ["X", "", "O"],
      ["", "X", ""],
      ["", "", ""]
    ],
    "active_player": "JaneTTT"
  },
  "timestamp": 1727000006
}
```

The board is always a 3x3 list.

Each cell must be:

- `"X"`
- `"O"`
- `""`

### ERROR

Direction: Server -> Client

Sent when a client sends an invalid move or bad message.

```json
{
  "msg_type": "ERROR",
  "player_id": "SERVER",
  "payload": {
    "code": "OUT_OF_TURN",
    "message": "It is not your turn"
  },
  "timestamp": 1727000007
}
```

Possible MOVE error codes:

- `OUT_OF_TURN`
- `OUT_OF_BOUNDS`
- `CELL_TAKEN`

Malformed or unsupported messages can use:

- `MALFORMED`

An invalid move does not change the board or active player.

### DISCONNECT

Direction: Client -> Server

Sent when a player intentionally leaves the game.

```json
{
  "msg_type": "DISCONNECT",
  "player_id": "JohnTTT",
  "payload": {},
  "timestamp": 1727000010
}
```

If a player leaves during an active game, the other player wins by forfeit.

### GAME_OVER

Direction: Server -> Clients

Sent when there is a winner, draw, or forfeit.

```json
{
  "msg_type": "GAME_OVER",
  "player_id": "SERVER",
  "payload": {
    "result": "WIN",
    "winner": "JohnTTT",
    "reason": "THREE_IN_A_ROW"
  },
  "timestamp": 1727000012
}
```

Possible results:

- `WIN`
- `DRAW`
- `FORFEIT`

For a draw, `winner` is `null`.

Possible reasons:

- `THREE_IN_A_ROW` (result `WIN`)
- `BOARD_FULL` (result `DRAW`)
- `OPPONENT_DISCONNECTED` (result `FORFEIT`)

### Rematch

After `GAME_OVER`, if both players are still connected, the server waits 30 seconds:

- A client sends `CONNECT` to accept a rematch.
- A client sends `DISCONNECT` to decline.

If both send `CONNECT`, the server resets the board and sends a new `GAME_START`. If either declines or the timeout passes, the server ends the session.


## 4. TCP Stream Handling

TCP is a byte stream, so messages can arrive together or be split apart.

### Back-to-back messages

Two complete messages can arrive in one `recv()` call:

```text
{"msg_type":"CONNECT","player_id":"JohnTTT","payload":{},"timestamp":1727000000}\n{"msg_type":"MOVE","player_id":"JohnTTT","payload":{"row":0,"col":2},"timestamp":1727000005}\n
```

The receiver should split at each `\n` and parse both messages.

### Fragmented message

One message can arrive in multiple chunks:

```text
Chunk 1:
{"msg_type":"MOVE","player_id":"JohnTTT","payload":{"row":
```

```text
Chunk 2:
0,"col":2},"timestamp":1727000005}\n
```

The receiver keeps Chunk 1 in the buffer until Chunk 2 arrives.

## 5. Serialization Rules

When sending a message:

1. Build the JSON object using the required fields.
2. Convert it with `json.dumps` with no indent, so the message never contains a raw `\n` before the end.
3. Encode it using UTF-8.
4. Add exactly one `\n` at the end.
5. Send the full byte message.

Example:

```text
{"msg_type":"MOVE","player_id":"JohnTTT","payload":{"row":0,"col":2},"timestamp":1727000005}\n
```

Do not add extra message fields or message types.

## 6. Connection Termination

### Normal Disconnect

A client can send a `DISCONNECT` message before closing its socket.

The server should:

- stop using that client socket
- update the game state
- declare a forfeit if a game is active
- clean up the session

### TCP EOF

If:

```python
data = sock.recv(1024)
```

returns:

```python
b""
```

the client has closed the connection.

The server must stop reading from that socket and handle the disconnect.

### Socket Errors

An abrupt disconnect TCP RST, network drop, the server should handle:

- `ConnectionResetError`
- `BrokenPipeError`
- `ConnectionAbortedError`

The server should not keep retrying a dead socket.

## 7. Protocol Rules

- Only use the 8 message types listed in this file
- Every message must use `msg_type`, `player_id`, `payload`, and `timestamp`
- Every JSON message ends with `\n`
- Do not assume one `recv()` is one message
- Invalid moves send `ERROR`
- Invalid moves do not change the board or active player
- `recv()` returning `b""` means the client disconnected
- Do not add extra fields or message types that are not part of this design
