import threading
import json
import requests
import os
import random
import time
import chess  # Tracks real-time board positioning

# --- CONFIGURATION ---
TOKEN = os.environ.get("LICHESS_TOKEN", "YOUR_SECRET_TOKEN_HERE")
BOT_USERNAME = "Studyloversz-bot"

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json"
}

def send_chat_message(game_id, room, text):
    """Sends a chat message to the opponent or spectator room."""
    url = f"https://lichess.org/api/bot/game/{game_id}/chat"
    data = {"room": room, "text": text}
    try:
        requests.post(url, headers=HEADERS, json=data)
    except Exception as e:
        print(f"[{game_id}] Failed to send chat: {e}")

def make_lichess_move(game_id, move_str):
    """Sends the calculated move back to Lichess."""
    url = f"https://lichess.org/api/bot/game/{game_id}/move/{move_str}"
    try:
        response = requests.post(url, headers=HEADERS)
        if response.status_code == 200:
            print(f"[{game_id}] Played move: {move_str}")
        else:
            print(f"[{game_id}] Move failed ({response.status_code}): {response.text}")
    except Exception as e:
        print(f"[{game_id}] Error posting move: {e}")

def get_engine_move(moves_list):
    """
    Queries Lichess Cloud Database.
    Safely indexes variables and uses random legal moves as fallback.
    """
    board = chess.Board()
    for move in moves_list:
        try:
            board.push_uci(move)
        except Exception:
            pass
            
    if board.is_game_over():
        return None

    current_fen = board.fen()
    cloud_url = "https://lichess.org/api/cloud-eval"
    params = {"fen": current_fen, "multiPv": 3}
    
    # Prevents HTTP 425 Rate Limits
    time.sleep(0.4) 
    
    try:
        response = requests.get(cloud_url, params=params)
        if response.status_code == 200:
            data = response.json()
            pvs = data.get("pvs", [])
            
            if pvs:
                dice_roll = random.random()
                if dice_roll > 0.85 and len(pvs) >= 3:
                    chosen_pv = pvs[2]  # ~1800 Elo choice
                elif dice_roll > 0.70 and len(pvs) >= 2:
                    chosen_pv = pvs[1]  # ~2000 Elo choice
                else:
                    chosen_pv = pvs[0]  # Top line choice
                    
                best_move_line = chosen_pv.get("moves", "").split()
                if best_move_line:
                    move_candidate = best_move_line[0]
                    # Verify legality before returning
                    if chess.Move.from_uci(move_candidate) in board.legal_moves:
                        return move_candidate
    except Exception as e:
        print(f"Cloud Engine API error: {e}")

    # Safe dynamic fallback instead of a broken hardcoded string
    legal_moves = list(board.legal_moves)
    if legal_moves:
        return random.choice(legal_moves).uci()
        
    return None 

def play_game(game_id):
    """Streams individual match events. Breaks loop when game ends."""
    print(f"\n[GAME START] Thread spawned for game: {game_id}")
    url = f"https://lichess.org/api/bot/game/stream/{game_id}"
    
    try:
        response = requests.get(url, headers=HEADERS, stream=True)
    except Exception as e:
        print(f"[{game_id}] Stream connection failed: {e}")
        return
        
    bot_color = None
    sent_welcome = False

    for line in response.iter_lines():
        if not line:
            continue
            
        try:
            game_event = json.loads(line.decode('utf-8'))
        except Exception:
            continue

        # Stop thread if game status updates to finished/aborted/resigned
        if game_event.get('type') == 'gameState' and game_event.get('status') != 'started':
            print(f"[{game_id}] Match complete. Reason: {game_event.get('status')}")
            send_chat_message(game_id, "player", "Good game! Thanks for playing.")
            break

        if game_event.get('type') == 'gameFull':
            white_id = game_event['white'].get('id', '').lower()
            bot_color = 'white' if white_id == BOT_USERNAME.lower() else 'black'
            state = game_event['state']
            
            if state.get('status') != 'started':
                break
                
            if not sent_welcome:
                send_chat_message(game_id, "player", "Hello! I am a Python bot simulating a 2000 Elo engine. Good luck!")
                sent_welcome = True
                
        elif game_event.get('type') == 'gameState':
            state = game_event
        else:
            continue

        moves_played = state['moves'].strip().split() if state['moves'].strip() else []
        total_moves = len(moves_played)

        is_bot_turn = (total_moves % 2 == 0 and bot_color == 'white') or \
                      (total_moves % 2 != 0 and bot_color == 'black')

        if is_bot_turn:
            # Simulate a realistic human thinking delay
            time.sleep(random.uniform(0.6, 1.8))
            
            bot_move = get_engine_move(moves_played)
            if bot_move:
                make_lichess_move(game_id, bot_move)

def listen_to_events():
    """Listens to global challenges and game starts."""
    print(f"Starting global event listener for user: {BOT_USERNAME}")
    url = "https://lichess.org/api/stream/event"
    
    response = requests.get(url, headers=HEADERS, stream=True)
    
    for line in response.iter_lines():
        if not line:
            continue
            
        try:
            event = json.loads(line.decode('utf-8'))
        except Exception:
            continue

        if event.get('type') == 'challenge':
            challenge_id = event['challenge']['id']
            variant = event['challenge']['variant']['key']
            
            # Decline complex variants to avoid calculation crashes
            if variant != 'standard':
                print(f"[CHALLENGE] Declining variant '{variant}' for ID: {challenge_id}")
                requests.post(f"https://lichess.org/api/challenge/{challenge_id}/decline", headers=HEADERS)
                continue

            print(f"[CHALLENGE] Auto-accepting ID: {challenge_id}")
            accept_url = f"https://lichess.org/api/challenge/{challenge_id}/accept"
            requests.post(accept_url, headers=HEADERS)

        elif event.get('type') == 'gameStart':
            game_id = event['game']['id']
            game_thread = threading.Thread(target=play_game, args=(game_id,))
            game_thread.daemon = True
            game_thread.start()

if __name__ == "__main__":
    # Infinite outer recovery wrapper loop
    while True:
        try:
            listen_to_events()
        except Exception as global_err:
            print(f"Network stream disconnected: {global_err}. Reconnecting in 10 seconds...")
            time.sleep(10)
