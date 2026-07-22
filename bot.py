import threading
import json
import requests
import os
import random
import time
import queue
import shutil
import chess
import chess.engine
import chess.variant
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- CONFIGURATION ---
TOKEN = os.environ.get("LICHESS_TOKEN", "YOUR_SECRET_TOKEN_HERE")
BOT_USERNAME = "Studyloversz-bot"

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json"
}

# Supported variants mapping
SUPPORTED_VARIANTS = {
    'standard': chess.Board,
    'antichess': chess.variant.AntichessBoard,
    'atomic': chess.variant.AtomicBoard,
    'crazyhouse': chess.variant.CrazyhouseBoard,
    'horde': chess.variant.HordeBoard,
    'kingofthehill': chess.variant.KingOfTheHillBoard,
    'racingkings': chess.variant.RacingKingsBoard,
    'threecheck': chess.variant.ThreeCheckBoard,
}

# Thread-safe job queue for engine calculations
engine_queue = queue.Queue()

# --- FAKE SERVER FOR RENDER ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Lichess Bot & Fake Server are fully active!")

    def log_message(self, format, *args):
        return  # Suppress internal server logs to keep console clean

def run_fake_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    print(f"[RENDER] Fake health check server listening on port {port}")
    server.serve_forever()

# --- GAME ACTIONS ---
def send_chat_message(game_id, room, text):
    """Sends a chat message to the opponent or spectator room."""
    url = f"https://lichess.org/api/bot/game/{game_id}/chat"
    data = {"room": room, "text": text}
    try:
        requests.post(url, headers=HEADERS, json=data, timeout=5)
    except Exception as e:
        print(f"[{game_id}] Failed to send chat: {e}")

def make_lichess_move(game_id, move_str):
    """Sends the calculated move back to Lichess."""
    url = f"https://lichess.org/api/bot/game/{game_id}/move/{move_str}"
    try:
        response = requests.post(url, headers=HEADERS, timeout=5)
        if response.status_code == 200:
            print(f"[{game_id}] Played move: {move_str}")
        else:
            print(f"[{game_id}] Move failed ({response.status_code}): {response.text}")
    except Exception as e:
        print(f"[{game_id}] Error posting move: {e}")

# --- ENGINE DETECTION ---
def find_engine_binary(engine_name):
    """Finds the engine binary in system paths."""
    resolved_path = shutil.which(engine_name)
    
    if resolved_path:
        print(f"[ENGINE] Successfully located {engine_name} binary at: {resolved_path}")
        return resolved_path
    
    # Fallback paths for different engines
    fallback_paths = {
        'stockfish': ["./stockfish", "/usr/games/stockfish", "/usr/bin/stockfish", "/usr/local/bin/stockfish"],
        'fairy-stockfish': ["./fairy-stockfish", "/usr/games/fairy-stockfish", "/usr/bin/fairy-stockfish", "/usr/local/bin/fairy-stockfish"],
        'fairyfish': ["./fairyfish", "/usr/games/fairyfish", "/usr/bin/fairyfish", "/usr/local/bin/fairyfish"]
    }
    
    for path in fallback_paths.get(engine_name, []):
        if os.path.exists(path):
            print(f"[ENGINE] Fallback found {engine_name} binary at: {path}")
            return path
    
    return None

# --- BACKGROUND ENGINE WORKER ---
def stockfish_worker():
    """Dedicated background thread handling all Stockfish calculations sequentially."""
    print("[ENGINE] Initializing engine instances...")
    
    # Initialize Normal Stockfish (for standard chess)
    stockfish_path = find_engine_binary("stockfish")
    if not stockfish_path:
        print("[CRITICAL] Could not locate Stockfish binary!")
        return
    
    # Initialize Fairy Stockfish (for variants)
    fairy_stockfish_path = find_engine_binary("fairy-stockfish")
    if not fairy_stockfish_path:
        fairy_stockfish_path = find_engine_binary("fairyfish")
    
    if fairy_stockfish_path:
        print("[ENGINE] Fairy Stockfish found - variant support enabled")
    else:
        print("[WARNING] Fairy Stockfish not found - only standard chess will be optimal")

    try:
        normal_engine = chess.engine.SimpleEngine.popen_uci(stockfish_path)
        normal_engine.configure({"Skill Level": 20, "Hash": 64, "Threads": 1})
        print("[ENGINE] Normal Stockfish is fully loaded and ready.")
    except Exception as e:
        print(f"[CRITICAL] Failed to start Normal Stockfish: {e}")
        return

    fairy_engine = None
    if fairy_stockfish_path:
        try:
            fairy_engine = chess.engine.SimpleEngine.popen_uci(fairy_stockfish_path)
            fairy_engine.configure({"Skill Level": 20, "Hash": 64, "Threads": 1})
            print("[ENGINE] Fairy Stockfish is fully loaded and ready.")
        except Exception as e:
            print(f"[WARNING] Failed to start Fairy Stockfish: {e}")
            print("[WARNING] Will use Normal Stockfish for all games.")

    while True:
        game_id, moves_list, callback, variant_key = engine_queue.get()
        try:
            # Select appropriate engine
            if variant_key == 'standard':
                engine = normal_engine
            else:
                engine = fairy_engine if fairy_engine else normal_engine
                if fairy_engine is None and variant_key != 'standard':
                    print(f"[{game_id}] WARNING: Using Normal Stockfish for {variant_key} (not optimal)")

            # Create board based on variant
            board_class = SUPPORTED_VARIANTS.get(variant_key, chess.Board)
            board = board_class()
            
            for move in moves_list:
                try:
                    board.push_uci(move)
                except Exception:
                    pass

            if board.is_game_over():
                callback(None)
                engine_queue.task_done()
                continue

            result = engine.play(board, chess.engine.Limit(time=0.1))
            best_move = result.move

            if best_move and board.is_legal(best_move):
                print(f"[{game_id}] Engine generated valid move: {best_move.uci()}")
                callback(best_move.uci())
            else:
                legal_moves = list(board.legal_moves)
                if legal_moves:
                    fallback_move = random.choice(legal_moves).uci()
                    print(f"[{game_id}] Panic fallback triggered. Selected move: {fallback_move}")
                    callback(fallback_move)
                else:
                    callback(None)

        except Exception as err:
            print(f"[{game_id}] Engine error during analysis: {err}")
            callback(None)
        finally:
            engine_queue.task_done()

# --- INDIVIDUAL GAME THREAD ---
def play_game(game_id, variant_key='standard'):
    """Streams individual match events. Breaks loop when game ends."""
    print(f"\n[GAME START] Thread spawned for game: {game_id} | Variant: {variant_key}")
    url = f"https://lichess.org/api/bot/game/stream/{game_id}"
    
    try:
        response = requests.get(url, headers=HEADERS, stream=True, timeout=None)
    except Exception as e:
        print(f"[{game_id}] Stream connection failed: {e}")
        return
        
    bot_color = None
    opponent = None
    sent_welcome = False

    # helper to normalize player objects
    def _parse_player_info(player_obj):
        if not isinstance(player_obj, dict):
            return {"id": "", "name": "", "rating": None, "title": ""}
        # common shapes: {'id': 'username', 'name': 'Full Name', 'rating': 1500}
        player_id = player_obj.get('id') or (player_obj.get('user') or {}).get('id') or ""
        return {
            "id": player_id,
            "name": player_obj.get('name', "") or "",
            "rating": player_obj.get('rating'),
            "title": player_obj.get('title', "") or ""
        }

    for line in response.iter_lines():
        if not line:
            continue
            
        try:
            game_event = json.loads(line.decode('utf-8'))
        except Exception:
            continue

        event_type = game_event.get('type')
        state = None
        
        if event_type == 'gameFull':
            # parse players and determine opponent/bot color
            white_player = _parse_player_info(game_event.get('white', {}))
            black_player = _parse_player_info(game_event.get('black', {}))

            if white_player["id"] and white_player["id"].lower() == BOT_USERNAME.lower():
                bot_color = 'white'
                opponent = black_player
            elif black_player["id"] and black_player["id"].lower() == BOT_USERNAME.lower():
                bot_color = 'black'
                opponent = white_player
            else:
                # fallback: if bot username not present, try to infer from provided IDs
                bot_color = None
                opponent = black_player if white_player["id"] else white_player

            state = game_event['state']
            print(f"[{game_id}] Match configuration locked. Bot Color side: {bot_color.upper() if bot_color else 'UNKNOWN'}")
            if opponent and opponent.get('id'):
                print(f"[{game_id}] Opponent found: @{opponent.get('id')} (name={opponent.get('name')}, rating={opponent.get('rating')}, title={opponent.get('title')})")

        elif event_type == 'gameState':
            state = game_event
            if bot_color is None:
                print(f"[{game_id}] Stream reconnected mid-game. Fetching true match details...")
                try:
                    export_url = f"https://lichess.org/api/bot/game/{game_id}"
                    meta_resp = requests.get(export_url, headers=HEADERS, timeout=5)
                    if meta_resp.status_code == 200:
                        meta_data = meta_resp.json()
                        white_player = _parse_player_info(meta_data.get('white', {}))
                        black_player = _parse_player_info(meta_data.get('black', {}))

                        if white_player["id"] and white_player["id"].lower() == BOT_USERNAME.lower():
                            bot_color = 'white'
                            opponent = black_player
                        elif black_player["id"] and black_player["id"].lower() == BOT_USERNAME.lower():
                            bot_color = 'black'
                            opponent = white_player

                        print(f"[{game_id}] Recovered color profile safely: {bot_color.upper() if bot_color else 'UNKNOWN'}")
                        if opponent and opponent.get('id'):
                            print(f"[{game_id}] Recovered opponent: @{opponent.get('id')}")
                except Exception as ex:
                    print(f"[{game_id}] Error recovering color profile: {ex}")
        else:
            continue

        if not state:
            continue

        if state.get('status') != 'started':
            opponent_tag = f"@{opponent['id']}" if opponent and opponent.get('id') else ""
            print(f"[{game_id}] Match complete. Reason: {state.get('status')}")
            # include opponent mention if available
            send_chat_message(game_id, "player", f"Good game! Thanks for playing. {opponent_tag}")
            break

        if event_type == 'gameFull' and not sent_welcome:
            if opponent and opponent.get('id'):
                send_chat_message(game_id, "player", f"Hello @{opponent.get('id')}! Engine Mode active ({variant_key}). Good luck!")
            else:
                send_chat_message(game_id, "player", f"Hello! Engine Mode active ({variant_key}). Good luck!")
            sent_welcome = True

        moves_played = state['moves'].strip().split() if state['moves'].strip() else []
        total_moves = len(moves_played)

        if bot_color is None:
            print(f"[{game_id}] Warning: Skipping move check because bot color is unknown.")
            continue

        is_bot_turn = (total_moves % 2 == 0 and bot_color == 'white') or \
                      (total_moves % 2 != 0 and bot_color == 'black')

        if is_bot_turn:
            print(f"[{game_id}] Bot turn detected (Move #{total_moves + 1}). Queueing engine evaluation...")
            def handle_move_result(move_uci):
                if move_uci:
                    make_lichess_move(game_id, move_uci)

            engine_queue.put((game_id, moves_played, handle_move_result, variant_key))

# --- GLOBAL EVENT LISTENER ---
def listen_to_events():
    """Listens to global challenges and game starts with heavy diagnostic tracking."""
    print(f"Starting global event listener for user: {BOT_USERNAME}")
    print(f"[VARIANTS] Supported: {', '.join(SUPPORTED_VARIANTS.keys())}")
    url = "https://lichess.org/api/stream/event"
    
    while True:
        try:
            response = requests.get(url, headers=HEADERS, stream=True, timeout=None)
            print("[SERVER] Stream connection successfully established with Lichess pipelines.")
            
            for line in response.iter_lines():
                if not line:
                    continue
                try:
                    event = json.loads(line.decode('utf-8'))
                except Exception as parse_err:
                    print(f"[STREAM ERROR] Failed to parse stream line data: {parse_err}")
                    continue

                event_type = event.get('type')
                print(f"[STREAM EVENT] Received incoming packet notification type: '{event_type}'")

                if event_type == 'challenge':
                    challenge_data = event['challenge']
                    challenge_id = challenge_data['id']
                    challenger_name = challenge_data.get('challenger', {}).get('id', 'Unknown')
                    variant = challenge_data['variant']['key']
                    is_rated = challenge_data.get('rated', False)
                    
                    print(f"[CHALLENGE RECEIVED] ID: {challenge_id} from user: @{challenger_name} | Variant: {variant} | Rated: {is_rated}")
                    
                    if variant not in SUPPORTED_VARIANTS:
                        print(f"[CHALLENGE DECLINED] Reason: Variant '{variant}' is not supported. Supported variants: {', '.join(SUPPORTED_VARIANTS.keys())}")
                        requests.post(f"https://lichess.org/api/challenge/{challenge_id}/decline", headers=HEADERS, json={"reason": "variant"}, timeout=5)
                        continue

                    print(f"[CHALLENGE ACCEPTED] Variant '{variant}' is supported. Processing accept call to ID: {challenge_id}...")
                    accept_url = f"https://lichess.org/api/challenge/{challenge_id}/accept"
                    accept_res = requests.post(accept_url, headers=HEADERS, timeout=5)
                    print(f"[CHALLENGE RESPONSE] Lichess server accept action status code: {accept_res.status_code}")

                elif event_type == 'gameStart':
                    game_id = event['game']['id']
                    variant_key = event.get('game', {}).get('variant', {}).get('key', 'standard')
                    print(f"[MATCH INITIALIZED] Spawning independent execution thread for game ID: {game_id} | Variant: {variant_key}")
                    game_thread = threading.Thread(target=play_game, args=(game_id, variant_key), daemon=True)
                    game_thread.start()
                    
        except Exception as global_err:
            print(f"[SYSTEM CRITICAL] Network or stream infrastructure drop: {global_err}")
            print("[SYSTEM] Attempting automatic connection reconstruction in 5 seconds...")
            time.sleep(5)


# --- EXECUTION ---
if __name__ == "__main__":
    if not TOKEN or TOKEN == "YOUR_SECRET_TOKEN_HERE":
        print("[CRITICAL] Authentication Failed: LICHESS_TOKEN variable is completely missing or empty!")
        exit(1)
    
    print(f"[SYSTEM] Validating environment credentials for account: {BOT_USERNAME}")
    
    try:
        test_res = requests.get("https://lichess.org/api/account", headers=HEADERS, timeout=5)
        
        if test_res.status_code == 401:
            print("[CRITICAL] Lichess rejected token! Error 401: Unauthorized. Check your LICHESS_TOKEN variable.")
            exit(1)
        elif test_res.status_code != 200:
            print(f"[CRITICAL] Lichess API error! Server response ({test_res.status_code}): {test_res.text}")
            exit(1)
        
        account_data = test_res.json()
        print(f"[SUCCESS] Successfully authenticated account on Lichess! Connected to: {account_data.get('id')}")
        
        if account_data.get('title') != 'BOT':
            print("[WARNING] Your account does NOT have the purple BOT badge on Lichess yet.")
            print("[WARNING] Run this command in your computer terminal to upgrade it permanently:")
            print(f'curl -d "" https://lichess.org/api/bot/account/upgrade -H "Authorization: Bearer {TOKEN}"')
        
    except Exception as api_err:
        print(f"[CRITICAL] Failed to communicate with Lichess verification servers: {api_err}")
        exit(1)
    
    # 1. Start the fake health check server thread for Render compatibility
    render_server = threading.Thread(target=run_fake_server, daemon=True)
    render_server.start()
    
    # 2. Start the local engine processing pipeline thread
    worker_thread = threading.Thread(target=stockfish_worker, daemon=True)
    worker_thread.start()
    
    try:
        listen_to_events()
    except KeyboardInterrupt:
        print("\n[SHUTDOWN] Bot execution halted manually.")
    finally:
        print("[SHUTDOWN] Clean exit completed.")
