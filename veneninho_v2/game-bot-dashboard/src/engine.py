"""Motor do bot: os mesmos módulos dos scripts da raiz (battle.py, captura.py,
cave bot.py), mas rodando em threads que podem ser ligadas/desligadas pelo painel."""

import base64
import copy
import ctypes
import datetime as dt
import json
import os
import re
import shutil
import sqlite3
import threading
import time
from collections import deque
from ctypes import wintypes
from pathlib import Path

import cv2
import numpy as np

try:
    import pyautogui as pg
    pg.FAILSAFE = True
    pg.useImageNotFoundException(False)
except Exception as e:  # pragma: no cover
    pg = None
    PG_ERROR = str(e)

try:
    import interception as ic
    from interception import inputs as ic_inputs
except Exception as e:  # pragma: no cover
    ic = None
    ic_inputs = None
    IC_ERROR = str(e)

# O pacote "interception" (bindings cffi, outro projeto) instala na mesma pasta do
# "interception-python" e o _utils.pyd dele sombreia o _utils.py: teclado funciona,
# mas qualquer clique com coordenada quebra com "has no attribute 'to_interception_coordinate'".
IC_CONFLICT = bool(ic_inputs and not hasattr(getattr(ic_inputs, "_utils", None), "to_interception_coordinate"))
IC_FIX_CMD = "pip uninstall -y interception interception-python && pip install interception-python==1.13.6"


PANEL_TITLE = "Veneno do Pokémon"

DEFAULT_CONFIG = {
    "battle": {
        "attack_keys": ["e", "q"],
        "interval": 0.5,
        "confidence": 0.9,
        "pokemon_list_region": [],
        "pause_targets": [],
        "target_confidence": 0.85,
        "empty_region": [],
        "auto_target_enabled": False,
        "auto_target_key": "",
        "long_battle_enabled": False,
        "long_battle_after": 0,
        "long_battle_key": "f3",
        "long_battle_repeats": 1,
    },
    "capture": {
        "key": "1",
        "keys": {},  # sobrescritas por arquivo: {"pokemon.png": "2"}
        "interval": 0.5,
        "confidence": 0.75,
        "region": [3, 28, 1910, 993],
        "targets": ["croa.png", "croa_2.png"],
        "alert_on": [],  # avisa com som quando aparecer, mesmo sem capturar
    },
    "cavebot": {
        "walk_time": 9,
        "engage_delay": 0,
        "battle_skip_after": 0,
        "confidence": 0.8,
        "map_region": [1730, 57, 182, 270],
        "hp_pixel": [1748, 286],
        "hp_color": [255, 0, 0],
        "route": [],  # [{"name": "1.png", "time": 9}, ...]; vazio = todos de imags/map em ordem
    },
    "heal": {
        "key": "f1",
        "buff_key": "",
        "buff_interval": 30,
        "follow_enabled": False,
        "follow_key": "",
        "follow_interval": 5,
        "order_enabled": False,
        "order_key": "",
        "order_point": [],
        "order_interval": 5,
        "pixel": [],  # ponto da barra de vida do seu Pokémon; vazio = não configurado
        "color": [],
        "cooldown": 2,
        "interval": 0.3,
    },
    "safety": {
        "pause_unfocused": True,
        "fight_timeout": 60,
        "max_misses": 5,
    },
    "alerts": {
        "sound": True,
    },
    "macro": {
        "max_seconds": 120,
        "name": "",
        "loop": False,
        "arrows_only": False,
    },
    "switch": {
        "slots": [],
        "interval": 60,
        "mode": "battle",  # battle, battle_switch ou switch
    },
    "pairing": {
        "region": [],  # recorte que contém as duas fileiras de três ícones
    },
    "loot": {
        "targets": ["nw", "n", "ne", "w", "e", "sw", "s", "se"],
        "check_interval": 5,
        "center": [],
        "north": [],
        "east": [],
        "window_sample_region": [],
        "anchor_region": [],
        "anchor_offset": [],
        "first_slot_offset": [],
    },
    "hotkeys": {"battle": "", "capture": "", "cavebot": "", "heal": "", "switch": "", "combat": "", "pairing": "", "macro": "", "loot": ""},
    "module_order": ["battle", "capture", "cavebot", "heal", "switch", "combat", "pairing", "loot", "macro"],
    "window_title": "otPokemon | Lisalon | South America",
    "window_ref": None,  # canto da janela do jogo quando as coordenadas foram marcadas
    "stop_hotkey": "F12",
}

PICK_HOTKEY = "F8"
VK_CODES = {f"F{i}": 0x6F + i for i in range(1, 13)}
VK_ESCAPE = 0x1B
MACRO_KEYS = {
    **{code: chr(code).lower() for code in range(0x41, 0x5B)},
    **{code: chr(code) for code in range(0x30, 0x3A)},
    **{0x70 + i: f"f{i + 1}" for i in range(12)},
    0x08: "backspace", 0x09: "tab", 0x0D: "enter", 0x1B: "esc",
    0x20: "space", 0x25: "left", 0x26: "up", 0x27: "right", 0x28: "down",
}
MACRO_KEYS.pop(0x77, None)
MACRO_MOUSE_BUTTONS = {0x01: "left"}
MAX_MACRO_ACTIONS = 20000


MODULES = {
    "battle": "Batalha",
    "capture": "Captura",
    "cavebot": "Cavebot",
    "heal": "Cura",
    "switch": "Troca",
    "combat": "Combate",
    "pairing": "Minigame",
    "loot": "Loot",
    "macro": "Rota gravada",
}


def _merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _migrate(cfg):
    """Configs antigas guardavam a rota como lista de nomes em cavebot.waypoints."""
    cave = cfg.get("cavebot", {})
    old = cave.pop("waypoints", None)
    if old and not cave.get("route"):
        cave["route"] = [{"name": n, "time": cave.get("walk_time", 9)} for n in old]
    return cfg


def _safe_name(name):
    name = re.sub(r"[^\w\- ]", "", str(name).strip(), flags=re.UNICODE).strip().replace(" ", "_")
    return name.lower()[:40] or "pokemon"


def _read_image(path):
    """cv2.imread não abre caminhos com acento no Windows; isso abre."""
    data = np.fromfile(str(path), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None


def _write_png(path, img):
    ok, buf = cv2.imencode(".png", img)
    if ok:
        buf.tofile(str(path))
    return ok


def _jpeg_data_uri(img, max_w):
    h, w = img.shape[:2]
    if w > max_w:
        img = cv2.resize(img, (max_w, int(h * max_w / w)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return "data:image/jpeg;base64," + base64.b64encode(buf).decode()


# Instância própria da user32 com tipos declarados (HWND é 64 bits); não mexe na do pygetwindow
_user32 = ctypes.WinDLL("user32") if hasattr(ctypes, "WinDLL") else None
if _user32:
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.FindWindowW.restype = wintypes.HWND
    _user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    _user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]


def _window_text(hwnd):
    if not hwnd:
        return ""
    n = _user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    _user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


class Stats:
    """Histórico em SQLite (estatisticas.db) + contadores da sessão atual."""

    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS eventos (quando TEXT DEFAULT (datetime('now','localtime')), tipo TEXT, nome TEXT, modulo TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS tempo (quando TEXT DEFAULT (datetime('now','localtime')), modulo TEXT, segundos REAL)")
        self.session_start = time.time()
        self.kills = 0
        self.heals = 0
        self.balls = {}
        self.run_seconds = {name: 0.0 for name in MODULES}
        self.started = {}

    def _db(self):
        return sqlite3.connect(str(self.path))

    def _exec(self, sql, args):
        with self.lock:
            try:
                with self._db() as db:
                    db.execute(sql, args)
            except sqlite3.Error:
                pass

    def kill(self, module):
        self.kills += 1
        self._exec("INSERT INTO eventos (tipo, nome, modulo) VALUES ('batalha', NULL, ?)", (module,))

    def ball(self, pokemon, module):
        self.balls[pokemon] = self.balls.get(pokemon, 0) + 1
        self._exec("INSERT INTO eventos (tipo, nome, modulo) VALUES ('pokebola', ?, ?)", (pokemon, module))

    def heal(self):
        self.heals += 1
        self._exec("INSERT INTO eventos (tipo, nome, modulo) VALUES ('cura', NULL, 'heal')", ())

    def module_on(self, name):
        self.started[name] = time.time()

    def module_off(self, name):
        t = self.started.pop(name, None)
        if t:
            secs = time.time() - t
            self.run_seconds[name] += secs
            self._exec("INSERT INTO tempo (modulo, segundos) VALUES (?, ?)", (name, secs))

    def snapshot(self, days=14):
        now = time.time()
        run = {n: s + (now - self.started[n] if n in self.started else 0) for n, s in self.run_seconds.items()}
        since = f"-{days - 1} days"
        with self.lock, self._db() as db:
            total_balls = dict(db.execute("SELECT nome, COUNT(*) FROM eventos WHERE tipo='pokebola' GROUP BY nome").fetchall())
            total_kills = db.execute("SELECT COUNT(*) FROM eventos WHERE tipo='batalha'").fetchone()[0]
            day_events = db.execute(
                "SELECT date(quando), tipo, COUNT(*) FROM eventos WHERE date(quando) >= date('now','localtime',?) GROUP BY 1, 2",
                (since,)).fetchall()
            day_time = dict(db.execute(
                "SELECT date(quando), SUM(segundos) FROM tempo WHERE date(quando) >= date('now','localtime',?) GROUP BY 1",
                (since,)).fetchall())

        today = dt.date.today()
        daily = []
        for i in range(days - 1, -1, -1):
            d = (today - dt.timedelta(days=i)).isoformat()
            ev = {t: c for day, t, c in day_events if day == d}
            secs = day_time.get(d, 0) or 0
            if i == 0:  # inclui o que está rodando agora
                secs += sum(now - t for t in self.started.values())
            daily.append({"day": d, "balls": ev.get("pokebola", 0), "kills": ev.get("batalha", 0), "seconds": secs})
        return {
            "session": {"seconds_open": now - self.session_start, "run_seconds": run, "kills": self.kills,
                        "heals": self.heals, "balls": self.balls},
            "total": {"kills": total_kills, "balls": total_balls},
            "daily": daily,
        }

    def reset_history(self):
        with self.lock, self._db() as db:
            db.execute("DELETE FROM eventos")
            db.execute("DELETE FROM tempo")
        self.kills = 0
        self.heals = 0
        self.balls = {}
        self.run_seconds = {name: 0.0 for name in MODULES}
        self.session_start = time.time()
        self.started = {n: time.time() for n in self.started}


class BotEngine:
    def __init__(self, root: Path, config_path: Path):
        self.root = Path(root)
        self.img = self.root / "imags"
        self.config_path = Path(config_path)
        self.profiles_dir = self.root / "perfis"
        self.logs_dir = self.root / "logs"
        self.config = self._load_config()
        self.stats = Stats(self.root / "estatisticas.db")

        self._logs = deque(maxlen=800)
        self._seq = 0
        self._log_lock = threading.Lock()

        self._threads = {}
        self._stops = {}
        self._stop_reasons = {}
        self._route_record_stop = None
        self._route_record_thread = None
        self._status = {name: "Aguardando ativa\u00e7\u00e3o" for name in MODULES}
        self._ic_ready = False
        self._ic_lock = threading.Lock()
        self._mouse_action_lock = threading.RLock()
        self._capture_order_lock = threading.RLock()
        self._macro_action_lock = threading.RLock()
        self._templates = {}
        self._pairing_model = None
        self._pairing_preprocess = None
        self._pairing_device = None
        self._pairing_pause = threading.Event()
        self._loot_pause = threading.Event()
        self._pause_state_lock = threading.Lock()
        self._paused_modules = set()
        self._macro_pause = threading.Event()
        self._macro_pause_lock = threading.Lock()
        self._macro_pause_reasons = set()
        self._macro_pause_reasons_by_category = {name: set() for name in ("battle", "capture", "minigame", "target")}
        self._macro_resume_after = {name: 0.0 for name in ("battle", "capture", "minigame", "target")}
        self._macro_capture_detector_lock = threading.Lock()
        self._macro_capture_detector_checked_at = 0.0
        self._macro_capture_detector_cache = False
        self._macro_capture_detector_misses = 0
        self._macro_capture_last_seen_at = 0.0
        self._battle_detector_lock = threading.Lock()
        self._battle_detector_state = True
        self._battle_detector_candidate = None
        self._battle_detector_candidate_count = 0
        self._battle_detector_checked_at = 0.0
        self._battle_detector_cache = True
        self._battle_targets_lock = threading.Lock()
        self._battle_targets_checked_at = 0.0
        self._battle_targets_detected = False
        self._battle_target_last_seen_at = 0.0
        self._long_battle_lock = threading.Lock()
        self._long_battle_started_at = None
        self._long_battle_triggered = False
        self._auto_target_lock = threading.Lock()
        self._auto_target_last_pressed_at = 0.0
        self._auto_target_active = False
        self._health = {"game": False, "focused": False, "driver": False}
        self._game_rect = None  # (left, top, width, height) da janela do jogo
        self._pick = None
        self._deadline = None
        self._alerted = {}
        self._macro_lock = threading.RLock()
        self._macro_recording = False
        self._macro_record_stop = None
        self._macro_record_thread = None
        self._macro_record_started = 0.0
        self._macro_record_duration = 0.0
        self._macro_record_name = ""
        self._macro_actions = []
        self._macro_playing = False
        self._macro_play_loop = False
        self._macro_play_options = self._default_macro_options()
        self._macro_record_options = self._default_macro_options()
        self._macro_record_arrows_only = False
        self._macro_play_stop = None
        self._macro_play_thread = None
        self._macro_battle_started_at = None
        self._macro_battle_skip_logged = False
        self._cavebot_ignored_battle_active = False

        threading.Thread(target=self._hotkey_watcher, daemon=True).start()
        threading.Thread(target=self._health_watcher, daemon=True).start()

        if pg is None:
            self.log(f"pyautogui não carregou: {PG_ERROR}", "error")
        if ic is None:
            self.log(f"interception não carregou: {IC_ERROR}", "error")
        elif IC_CONFLICT:
            self.log("Tem outro pacote 'interception' instalado por cima do interception-python. "
                     f"Os cliques vão funcionar, mas para corrigir de vez rode: {IC_FIX_CMD}", "warn")
        self.log("Painel pronto.", "success")

    # ---------- config ----------
    def _load_config(self):
        try:
            with open(self.config_path, encoding="utf-8") as f:
                return _merge(DEFAULT_CONFIG, _migrate(json.load(f)))
        except Exception:
            return copy.deepcopy(DEFAULT_CONFIG)

    def save_config(self, new_config, quiet=False):
        new_config = _migrate(dict(new_config))
        # a referência da janela é gerenciada pelo motor; o painel pode estar com uma cópia velha
        if not new_config.get("window_ref") and self.config.get("window_ref"):
            new_config["window_ref"] = self.config["window_ref"]
        self.config = _merge(DEFAULT_CONFIG, new_config)
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=2, ensure_ascii=False)
        if not quiet:
            self.log("Configurações salvas.", "success")
        return self.config

    # ---------- perfis ----------
    def list_profiles(self):
        if not self.profiles_dir.exists():
            return []
        return sorted(p.stem for p in self.profiles_dir.glob("*.json"))

    def save_profile(self, name, config):
        name = str(name).strip()
        if not name or re.search(r'[\\/:*?"<>|]', name):
            raise ValueError("Nome de perfil inválido.")
        config = dict(config, profile=name)
        self.save_config(config, quiet=True)
        self.profiles_dir.mkdir(exist_ok=True)
        with open(self.profiles_dir / f"{name}.json", "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=2, ensure_ascii=False)
        self.log(f"Perfil '{name}' salvo.", "success")
        return self.config

    def load_profile(self, name):
        with open(self.profiles_dir / f"{name}.json", encoding="utf-8") as f:
            cfg = json.load(f)
        cfg["profile"] = name
        self.config["window_ref"] = None  # o perfil traz a própria referência
        self.save_config(cfg, quiet=True)
        self.log(f"Perfil '{name}' carregado.", "success")
        return self.config

    def delete_profile(self, name):
        (self.profiles_dir / f"{name}.json").unlink(missing_ok=True)
        if self.config.get("profile") == name:
            self.config.pop("profile", None)
            self.save_config(self.config, quiet=True)
        self.log(f"Perfil '{name}' excluído.", "info")
        return self.config

    # ---------- logs / estado ----------
    def log(self, msg, level="info", module=None):
        entry = {"time": time.strftime("%H:%M:%S"), "level": level, "module": module, "msg": msg}
        with self._log_lock:
            self._seq += 1
            entry["seq"] = self._seq
            self._logs.append(entry)
            try:
                self.logs_dir.mkdir(exist_ok=True)
                tag = f"[{MODULES[module]}] " if module else ""
                with open(self.logs_dir / f"{time.strftime('%Y-%m-%d')}.txt", "a", encoding="utf-8") as f:
                    f.write(f"{entry['time']} {level.upper():7} {tag}{msg}\n")
            except OSError:
                pass
        if module:
            self._status[module] = msg

    def open_logs_folder(self):
        self.logs_dir.mkdir(exist_ok=True)
        os.startfile(self.logs_dir)

    def state(self, since=0):
        with self._log_lock:
            logs = [l for l in self._logs if l["seq"] > since]
        out = {
            "running": {name: self.is_running(name) for name in MODULES},
            "status": dict(self._status),
            "health": dict(self._health),
            "logs": logs,
            "pick": None,
            "timer": max(0, self._deadline - time.time()) if self._deadline else None,
            "route_recording": bool(self._route_record_thread and self._route_record_thread.is_alive()),
        }
        pick = self._pick
        if pick:
            out["pick"] = {k: pick[k] for k in ("id", "kind", "target", "points", "result", "cancelled")}
            if pg is not None and pick["result"] is None and not pick["cancelled"]:
                x, y = pg.position()
                out["pick"]["mouse"] = {"x": x, "y": y, "rgb": list(self._pixel(x, y))}
        return out

    def is_running(self, name):
        t = self._threads.get(name)
        return bool(t and t.is_alive())

    def any_running(self):
        macro = self.macro_state()
        return any(self.is_running(n) for n in MODULES) or macro["recording"] or macro["playing"]

    # ---------- alertas ----------
    def alert(self, msg, level="error", module=None):
        self.log(msg, level, module)
        if not self.config["alerts"].get("sound"):
            return
        try:
            import winsound
            winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS | winsound.SND_ASYNC)
        except Exception:
            pass
        self._flash_panel()

    @staticmethod
    def _flash_panel():
        """Pisca o painel na barra de tarefas até alguém olhar."""
        class FLASHWINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("hwnd", wintypes.HWND), ("dwFlags", wintypes.DWORD),
                        ("uCount", wintypes.UINT), ("dwTimeout", wintypes.DWORD)]
        try:
            hwnd = _user32.FindWindowW(None, PANEL_TITLE)
            if hwnd:
                info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, 0x3 | 0xC, 0, 0)  # FLASHW_ALL | FLASHW_TIMERNOFG
                _user32.FlashWindowEx(ctypes.byref(info))
        except Exception:
            pass

    # ---------- timer ----------
    def set_timer(self, minutes=None, at=None):
        if at:
            h, m = (int(v) for v in str(at).split(":"))
            now = dt.datetime.now()
            target = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if target <= now:
                target += dt.timedelta(days=1)
            self._deadline = target.timestamp()
            self.log(f"Timer: desliga tudo às {target:%H:%M}.", "info")
        elif minutes:
            self._deadline = time.time() + float(minutes) * 60
            self.log(f"Timer: desliga tudo em {int(float(minutes))} min.", "info")
        else:
            if self._deadline:
                self.log("Timer cancelado.", "info")
            self._deadline = None
        return self._deadline

    # ---------- janela do jogo ----------
    def _find_game_windows(self):
        try:
            import pygetwindow as gw
        except Exception:
            return []
        title = self.config["window_title"].strip().casefold()
        return [w for w in gw.getAllWindows() if w.title and w.title.strip().casefold() == title]

    def _game_focused(self):
        try:
            title = _window_text(_user32.GetForegroundWindow())
        except Exception:
            return True
        return title.strip().casefold() == self.config["window_title"].strip().casefold()

    def _offset(self):
        """Quanto a janela do jogo andou desde que as coordenadas foram marcadas."""
        ref, rect = self.config.get("window_ref"), self._game_rect
        if not ref or not rect:
            return 0, 0
        return rect[0] - ref[0], rect[1] - ref[1]

    def _abs_region(self, region):
        if not region:
            return None
        dx, dy = self._offset()
        return [region[0] + dx, region[1] + dy, region[2], region[3]]

    def _abs_point(self, point):
        dx, dy = self._offset()
        return point[0] + dx, point[1] + dy

    def set_window_ref(self):
        """As coordenadas atuais estão certas para a janela onde ela está agora."""
        if not self._game_rect:
            raise RuntimeError("Abra o jogo primeiro: não achei a janela.")
        self.config["window_ref"] = list(self._game_rect[:2])
        self.save_config(self.config, quiet=True)
        self.log(f"Referência da janela: canto em {self._game_rect[0]}, {self._game_rect[1]}.", "success")
        return self.config

    def _health_watcher(self):
        was_found = False
        while True:
            try:
                wins = self._find_game_windows()
                w = next((w for w in wins if not w.isMinimized), wins[0] if wins else None)
                self._game_rect = (w.left, w.top, w.width, w.height) if w and not w.isMinimized else None
            except Exception:
                wins, self._game_rect = [], None
            found = bool(wins)
            self._health["game"] = found
            self._health["focused"] = found and self._game_focused()
            ctx = getattr(ic_inputs, "_g_context", None)
            self._health["driver"] = bool(ctx is not None and getattr(ctx, "valid", False))

            if found and self._game_rect and not self.config.get("window_ref"):
                self.config["window_ref"] = list(self._game_rect[:2])
                self.save_config(self.config, quiet=True)
            if was_found and not found and self.any_running():
                self.stop_all()
                self.alert("A janela do jogo sumiu: parei tudo.")
            was_found = found

            if self._deadline and time.time() >= self._deadline:
                self._deadline = None
                if self.any_running():
                    self.stop_all()
                    self.alert("Timer acabou: parei tudo.", "warn")
                else:
                    self.log("Timer acabou.", "info")
            time.sleep(1)

    def _wait_ready(self, stop, module):
        """Segura o módulo enquanto o jogo não estiver em primeiro plano. False se mandaram parar."""
        acknowledged_pause = False
        while not stop.is_set():
            pause_reason = None
            if module != "pairing" and self._pairing_pause.is_set():
                pause_reason = "minigame em execução"
            elif module not in ("pairing", "loot", "battle", "heal") and self._loot_pause.is_set():
                pause_reason = "varredura de loot"
            if pause_reason:
                if not acknowledged_pause:
                    with self._pause_state_lock:
                        self._paused_modules.add(module)
                        self._status[module] = f"Pausado: {pause_reason}"
                    acknowledged_pause = True
                stop.wait(0.1)
                continue
            if acknowledged_pause:
                with self._pause_state_lock:
                    self._paused_modules.discard(module)
                    self._status[module] = "Rodando"
                acknowledged_pause = False
            break
        if acknowledged_pause:
            with self._pause_state_lock:
                self._paused_modules.discard(module)
                self._status[module] = "Parado"
        if stop.is_set():
            return False
        if not self.config["safety"].get("pause_unfocused", True):
            return True
        paused = False
        while not stop.is_set() and not self._game_focused():
            if not paused:
                self.log("Pausado: o jogo não está em primeiro plano.", "warn", module)
                paused = True
            stop.wait(0.4)
        if paused and not stop.is_set():
            self.log("Jogo em foco, continuando.", "info", module)
        return not stop.is_set()

    # ---------- imagens ----------
    def capture_images(self):
        return sorted(p.name for p in (self.img / "captura").glob("*.png") if p.name != "tela.png")

    def battle_target_images(self):
        return sorted(p.name for p in (self.img / "battle" / "pokemon_list").glob("*.png"))

    def add_battle_target_image(self, name, region):
        base = _safe_name(name)
        folder = self.img / "battle" / "pokemon_list"
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / f"{base}.png"
        index = 2
        while dest.exists():
            dest = folder / f"{base}_{index}.png"
            index += 1
        image, _ = self._grab(region)
        if not _write_png(dest, image):
            raise RuntimeError("Não consegui salvar o recorte do Pokémon.")
        targets = self.config["battle"].setdefault("pause_targets", [])
        if dest.name not in targets:
            targets.append(dest.name)
        self.save_config(self.config, quiet=True)
        self.log(f"Alvo de batalha '{dest.stem}' adicionado e selecionado.", "success", "battle")
        return dest.name

    def remove_battle_target_image(self, name):
        safe_file = Path(name).name
        if safe_file != name or not safe_file.lower().endswith(".png"):
            raise ValueError("Alvo de batalha inválido.")
        self._move_to_trash("battle/pokemon_list", safe_file)
        targets = self.config["battle"].setdefault("pause_targets", [])
        self.config["battle"]["pause_targets"] = [target for target in targets if target != safe_file]
        self._templates.pop(f"battle/pokemon_list/{safe_file}", None)
        self.save_config(self.config, quiet=True)
        self.log(f"Alvo de batalha '{Path(safe_file).stem}' removido.", "info", "battle")

    def set_battle_empty_from_region(self, region):
        if len(region or []) != 4 or min(region[2:]) < 12:
            raise ValueError("Marque uma região válida que contenha a lista de batalha vazia.")
        image, _ = self._grab(self._abs_region(region))
        template_path = self.img / "battle" / "batalha_vazia.png"
        template_path.parent.mkdir(parents=True, exist_ok=True)
        if not _write_png(template_path, image):
            raise RuntimeError("Não consegui salvar a referência da lista vazia.")
        self.config["battle"]["empty_region"] = [int(value) for value in region]
        self._templates.pop("battle/batalha_vazia.png", None)
        with self._battle_detector_lock:
            self._battle_detector_state = True
            self._battle_detector_candidate = None
            self._battle_detector_candidate_count = 0
            self._battle_detector_checked_at = 0.0
            self._battle_detector_cache = True
        self.save_config(self.config, quiet=True)
        self.log("Referência da lista de batalha vazia definida.", "success", "battle")
        return self.config["battle"]["empty_region"]

    def _battle_target_is_present(self):
        config = self.config["battle"]
        region = config.get("pokemon_list_region") or []
        targets = config.get("pause_targets") or []
        if len(region) != 4 or min(region[2:]) < 1 or not targets:
            with self._battle_targets_lock:
                self._battle_targets_detected = False
            return False
        now = time.monotonic()
        with self._battle_targets_lock:
            if now - self._battle_targets_checked_at < 0.35:
                return self._battle_targets_detected
            self._battle_targets_checked_at = now
        try:
            absolute_region = self._abs_region(region)
            screenshot, offset = self._grab(absolute_region)
            confidence = float(config.get("target_confidence", 0.85))
            detected = any(
                self._locate(f"battle/pokemon_list/{Path(name).name}", confidence,
                             img=screenshot, offset=offset)
                for name in targets
            )
        except Exception as error:
            self.log(f"Falha ao verificar Pokémon na lista de batalha: {error}", "warn", "battle")
            detected = False
        with self._battle_targets_lock:
            self._battle_targets_detected = detected
            if detected:
                self._battle_target_last_seen_at = time.monotonic()
            return detected

    def map_waypoints(self):
        files = [p for p in (self.img / "map").glob("*.png") if p.stem.isdigit()]
        return [p.name for p in sorted(files, key=lambda p: int(p.stem))]

    def route(self):
        c = self.config["cavebot"]
        if c["route"]:
            return [(wp["name"], float(wp.get("time", c["walk_time"]))) for wp in c["route"]]
        return [(name, float(c["walk_time"])) for name in self.map_waypoints()]

    def add_capture_image(self, name, src_path=None, region=None):
        """Adiciona um Pokémon à pasta de captura, de um arquivo ou de um recorte da tela."""
        if src_path:
            img = _read_image(src_path)
            if img is None:
                raise ValueError("Não consegui abrir essa imagem.")
        else:
            img, _ = self._grab(region)
        base = _safe_name(name)
        dest = self.img / "captura" / f"{base}.png"
        n = 2
        while dest.exists():
            dest = self.img / "captura" / f"{base}_{n}.png"
            n += 1
        _write_png(dest, img)
        self.log(f"Pokémon '{dest.stem}' adicionado.", "success")
        return dest.name

    def add_waypoint(self, region):
        """Recorta um marcador do minimapa e salva como o próximo número da rota."""
        img, _ = self._grab(region)
        existing = [int(p.stem) for p in (self.img / "map").glob("*.png") if p.stem.isdigit()]
        removed = self.img / "map" / "removidos"
        if removed.exists():
            existing += [int(p.stem) for p in removed.glob("*.png") if p.stem.isdigit()]
        dest = self.img / "map" / f"{max(existing, default=0) + 1}.png"
        _write_png(dest, img)
        route = self.config["cavebot"]["route"]
        if route:
            route.append({"name": dest.name, "time": self.config["cavebot"]["walk_time"]})
            self.save_config(self.config, quiet=True)
        self.log(f"Ponto {dest.stem} adicionado à rota.", "success")
        return dest.name

    def _move_to_trash(self, folder, name):
        path = self.img / folder / Path(name).name
        trash = self.img / folder / "removidos"
        trash.mkdir(exist_ok=True)
        if path.exists():
            shutil.move(str(path), str(trash / path.name))
        return path

    def remove_capture_image(self, name):
        path = self._move_to_trash("captura", name)
        c = self.config["capture"]
        for key in ("targets", "alert_on"):
            if name in c[key]:
                c[key].remove(name)
        c.setdefault("keys", {}).pop(name, None)
        self.save_config(self.config, quiet=True)
        self.log(f"'{path.stem}' movido para imags/captura/removidos.", "info")

    def remove_waypoint(self, name):
        self._move_to_trash("map", name)
        c = self.config["cavebot"]
        c["route"] = [r for r in c["route"] if r["name"] != name]
        self.save_config(self.config, quiet=True)
        self.log(f"Ponto {Path(name).stem} movido para imags/map/removidos.", "info")

    def start_route_recording(self):
        """Grava cliques no minimapa como waypoints; o intervalo entre cliques
        define o tempo de caminhada do ponto anterior durante a reprodução."""
        if pg is None:
            raise RuntimeError("PyAutoGUI não está disponível para gravar a rota.")
        if self._route_record_thread and self._route_record_thread.is_alive():
            return False
        if self.is_running("cavebot"):
            raise RuntimeError("Pare o Cavebot antes de iniciar a gravação da rota.")
        templates = [self._template(f"map/{name}") for name in self.map_waypoints()]
        templates = [img for img in templates if img is not None]
        if not templates:
            raise ValueError("Adicione um ponto de mapa manualmente antes de gravar; ele define o tamanho do recorte dos novos pontos.")
        h, w = templates[0].shape[:2]
        stop = threading.Event()
        self._route_record_stop = stop
        self.config["cavebot"]["route"] = []
        self.save_config(self.config, quiet=True)

        def record():
            previous_down = False
            previous_click_at = None
            region = self._abs_region(self.config["cavebot"]["map_region"])
            left, top, width, height = region
            self.log("Gravação de rota ligada. Clique nos marcadores do minimapa; o intervalo entre cliques será salvo.", "info", "cavebot")
            while not stop.is_set():
                down = bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000)
                if down and not previous_down:
                    x, y = pg.position()
                    if left <= x < left + width and top <= y < top + height:
                        screen_w, screen_h = pg.size()
                        x0 = max(0, min(screen_w - w, int(x - w / 2)))
                        y0 = max(0, min(screen_h - h, int(y - h / 2)))
                        try:
                            img, _ = self._grab((x0, y0, w, h))
                            name = self._save_recorded_waypoint(img)
                            now = time.monotonic()
                            route = self.config["cavebot"]["route"]
                            if previous_click_at is not None and len(route) > 1:
                                route[-2]["time"] = round(max(0.5, now - previous_click_at), 1)
                            previous_click_at = now
                            self.save_config(self.config, quiet=True)
                            self.log(f"Ponto {Path(name).stem} gravado na rota.", "success", "cavebot")
                        except Exception as error:
                            self.log(f"Falha ao gravar clique do mapa: {error}", "warn", "cavebot")
                previous_down = down
                stop.wait(0.025)
            self.log("Gravação de rota parada.", "info", "cavebot")

        self._route_record_thread = threading.Thread(target=record, daemon=True, name="route-recording")
        self._route_record_thread.start()
        return True

    def stop_route_recording(self):
        if self._route_record_stop:
            self._route_record_stop.set()
        return True

    def _save_recorded_waypoint(self, img):
        existing = [int(p.stem) for p in (self.img / "map").glob("*.png") if p.stem.isdigit()]
        dest = self.img / "map" / f"{max(existing, default=0) + 1}.png"
        _write_png(dest, img)
        self.config["cavebot"]["route"].append({"name": dest.name, "time": float(self.config["cavebot"]["walk_time"])})
        return dest.name

    # ---------- detecção ----------
    def _template(self, rel_path):
        path = self.img / rel_path
        mtime = path.stat().st_mtime if path.exists() else None
        cached = self._templates.get(rel_path)
        if cached and cached[0] == mtime:
            return cached[1]
        tpl = _read_image(path) if mtime else None
        self._templates[rel_path] = (mtime, tpl)
        return tpl

    def _grab(self, region=None):
        region = tuple(int(v) for v in region) if region else None
        shot = pg.screenshot(region=region)
        img = cv2.cvtColor(np.array(shot), cv2.COLOR_RGB2BGR)
        return img, (region[0], region[1]) if region else (0, 0)

    @staticmethod
    def _match(tpl, img):
        if tpl is None or tpl.shape[0] > img.shape[0] or tpl.shape[1] > img.shape[1]:
            return 0.0, (0, 0)
        res = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(res)
        return float(score), loc

    def _locate(self, rel_path, confidence, region=None, img=None, offset=(0, 0)):
        """Centro da imagem na tela, ou None. Mesmo critério do pyautogui (TM_CCOEFF_NORMED),
        mas pega a melhor correspondência em vez da primeira."""
        tpl = self._template(rel_path)
        if tpl is None:
            return None
        if img is None:
            img, offset = self._grab(region)
        score, (x, y) = self._match(tpl, img)
        if score < confidence:
            return None
        th, tw = tpl.shape[:2]
        return pg.Point(offset[0] + x + tw // 2, offset[1] + y + th // 2)

    def _pixel(self, x, y):
        try:
            return tuple(pg.pixel(int(x), int(y)))
        except Exception:
            return (0, 0, 0)

    def test_detection(self, kind):
        """Uma busca só, com print marcando o que achou — para ajustar precisão e áreas."""
        if pg is None:
            raise RuntimeError("pyautogui não está disponível.")
        cfg = self.config
        if kind == "capture":
            region = self._abs_region(cfg["capture"]["region"])
            conf = cfg["capture"]["confidence"]
            names = [f"captura/{n}" for n in (cfg["capture"]["targets"] or self.capture_images())]
            max_w = 1000
        elif kind == "map":
            region = self._abs_region(cfg["cavebot"]["map_region"])
            conf = cfg["cavebot"]["confidence"]
            names = [f"map/{n}" for n, _ in self.route()]
            max_w = 600
        elif kind == "battle":
            configured_region = cfg["battle"].get("empty_region") or []
            region = self._abs_region(configured_region) if len(configured_region) == 4 else None
            conf = self._battle_empty_threshold()
            names = ["battle/batalha_vazia.png"]
            max_w = 1000
        elif kind == "pairing":
            region = self._abs_region(cfg["pairing"]["region"])
            if not region or len(region) != 4 or min(region[2:]) < 1:
                raise ValueError("Marque e salve a área completa do minigame em Ajustes > Minigame.")
            screen, _ = self._grab()
            present, presence_score, panel = self._minigame_present(screen)
            if not present:
                self.log("Teste do minigame: tela do minigame não detectada; nenhuma ação foi executada.",
                         "info", "pairing")
                return {"kind": kind, "active": False, "presence_score": round(presence_score, 3),
                        "confidence": 0, "image": _jpeg_data_uri(screen, 1000), "results": [],
                        "pairs": [], "offset": list(self._offset())}
            x, y, width, height = panel
            img = screen[y:y + height, x:x + width].copy()
            pairs, similarities, centers = self._resolve_pairs(img)
            canvas = img.copy()
            for top_idx, bottom_idx in pairs:
                a = tuple(map(int, centers[top_idx]))
                b = tuple(map(int, centers[3 + bottom_idx]))
                cv2.circle(canvas, a, max(5, img.shape[1] // 80), (90, 224, 143), 2)
                cv2.circle(canvas, b, max(5, img.shape[1] // 80), (90, 224, 143), 2)
                cv2.line(canvas, a, b, (90, 224, 143), 2)
            results = [{"name": f"Superior {i + 1} → inferior {j + 1}",
                        "score": round(float(similarities[i, j]), 3), "found": True}
                       for i, j in pairs]
            self.log("Teste do minigame: pareamento calculado; nenhuma ação foi executada.", "info", "pairing")
            return {"kind": kind, "active": True, "presence_score": round(presence_score, 3),
                    "confidence": 0, "image": _jpeg_data_uri(canvas, 1000),
                    "results": results, "pairs": [[i + 1, j + 1] for i, j in pairs],
                    "offset": list(self._offset()), "panel_region": panel}
        else:
            raise ValueError(kind)

        img, offset = self._grab(region)
        canvas = img.copy()
        scale = 2 if kind == "map" else 1
        if scale != 1:
            canvas = cv2.resize(canvas, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
        thick = max(2, int(img.shape[1] / max_w * 2))

        results = []
        for rel in names:
            tpl = self._template(rel)
            if tpl is None:
                results.append({"name": Path(rel).stem, "score": 0, "found": False, "missing": True})
                continue
            if kind == "battle":
                score, (x, y) = self._battle_empty_score(tpl, img)
            else:
                score, (x, y) = self._match(tpl, img)
            found = score >= conf
            th, tw = tpl.shape[:2]
            if found:
                p1 = (x * scale, y * scale)
                p2 = ((x + tw) * scale, (y + th) * scale)
                cv2.rectangle(canvas, p1, p2, (90, 224, 143), thick)
                cv2.putText(canvas, Path(rel).stem, (p1[0], max(12, p1[1] - 6)), cv2.FONT_HERSHEY_SIMPLEX,
                            0.45 * thick, (90, 224, 143), max(1, thick // 2), cv2.LINE_AA)
            results.append({
                "name": Path(rel).stem,
                "score": round(score, 3),
                "found": found,
                "x": offset[0] + x + tw // 2,
                "y": offset[1] + y + th // 2,
            })

        extra = None
        target_detected = None
        if kind == "battle":
            target_detected = self._battle_target_is_present()
            x, y = self._abs_point(cfg["cavebot"]["hp_pixel"])
            rgb = self._pixel(x, y)
            extra = {"hp_pixel": [x, y], "rgb": list(rgb), "matches": list(rgb) == list(cfg["cavebot"]["hp_color"])}
            cv2.circle(canvas, (int(x), int(y)), 10 * thick, (255, 112, 169), thick)

        self.log(f"Teste de detecção ({kind}): {sum(r['found'] for r in results)}/{len(results)} encontrados.", "info")
        return {
            "kind": kind,
            "confidence": conf,
            "image": _jpeg_data_uri(canvas, max_w),
            "results": sorted(results, key=lambda r: -r["score"]),
            "extra": extra,
            "target_detected": target_detected,
            "offset": list(self._offset()),
        }

    # ---------- calibração com o mouse ----------
    def start_pick(self, kind, target):
        """kind: 'point' (1 F8) ou 'region' (canto superior esquerdo + inferior direito)."""
        self._pick = {"id": time.time(), "kind": kind, "target": target, "points": [], "result": None, "cancelled": False}
        return PICK_HOTKEY

    def cancel_pick(self):
        if self._pick:
            self._pick["cancelled"] = True

    def clear_pick(self):
        self._pick = None

    def _pick_press(self):
        pick = self._pick
        if not pick or pick["result"] is not None or pick["cancelled"]:
            return
        x, y = pg.position()
        rgb = list(self._pixel(x, y))
        pick["points"].append([x, y])
        self._beep()
        # coordenadas salvas na config ficam no "referencial" da janela; recortes usam a tela real
        if pick["target"] in ("new_pokemon", "new_waypoint", "new_battle_target"):
            dx = dy = 0
        else:
            if not self.config.get("window_ref") and self._game_rect:
                self.config["window_ref"] = list(self._game_rect[:2])
                self.save_config(self.config, quiet=True)
            dx, dy = self._offset()
        if pick["kind"] == "point":
            pick["result"] = {"x": x - dx, "y": y - dy, "rgb": rgb}
        elif len(pick["points"]) == 2:
            (x1, y1), (x2, y2) = pick["points"]
            left, top = min(x1, x2), min(y1, y2)
            w, h = abs(x2 - x1), abs(y2 - y1)
            if w < 4 or h < 4:
                pick["points"] = []
                self.log("Área muito pequena, marque de novo os dois cantos.", "warn")
                return
            pick["result"] = {"region": [left - dx, top - dy, w, h]}

    @staticmethod
    def _beep():
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_OK)
        except Exception:
            pass

    # ---------- start / stop ----------
    def start(self, name):
        if name not in MODULES:
            return False
        if self.is_running(name):
            return True
        if pg is None or ic is None:
            self.log("Dependências faltando (veja os erros acima). Módulo não iniciado.", "error", name)
            return False
        if name in ("battle", "combat", "cavebot"):
            battle = self.config["battle"]
            if battle.get("long_battle_enabled"):
                after = float(battle.get("long_battle_after", 0) or 0)
                key = str(battle.get("long_battle_key") or "").strip().lower()
                repeats = int(battle.get("long_battle_repeats", 1))
                if not 1 <= after <= 3600 or key not in MACRO_KEYS.values() or not 1 <= repeats <= 20:
                    self.log("Configure uma tecla válida e de 1 a 20 repetições para batalhas longas.", "error", name)
                    return False
        if name == "heal":
            heal = self.config["heal"]
            has_hp_pixel = len(heal.get("pixel") or []) == 2
            has_buff_key = bool(str(heal.get("buff_key") or "").strip())
            follow_enabled = bool(heal.get("follow_enabled"))
            follow_key = str(heal.get("follow_key") or "").strip().lower()
            follow_interval = float(heal.get("follow_interval", 0) or 0)
            if follow_enabled and (follow_key not in MACRO_KEYS.values() or not 0.1 <= follow_interval <= 3600):
                self.log("Configure uma tecla válida e um intervalo entre 0,1 e 3600 segundos para o acompanhamento.", "error", name)
                return False
            order_enabled = bool(heal.get("order_enabled"))
            order_key = str(heal.get("order_key") or "").strip().lower()
            order_point = heal.get("order_point") or []
            order_interval = float(heal.get("order_interval", 0) or 0)
            if order_enabled and (
                order_key not in MACRO_KEYS.values()
                or len(order_point) != 2
                or not 0.1 <= order_interval <= 3600
            ):
                self.log("Configure a tecla, o ponto de clique e o intervalo da Order em Ajustes > Cura.", "error", name)
                return False
            if not has_hp_pixel and not has_buff_key and not follow_enabled and not order_enabled:
                self.log("Configure cura, buff, acompanhamento ou Order em Ajustes > Cura antes de ligar.", "error", name)
                return False
        if name == "pairing":
            region = self.config["pairing"].get("region")
            if not region or len(region) != 4 or min(region[2:]) < 1:
                self.log("Marque e salve a área do minigame em Ajustes > Minigame antes de ligar.", "error", name)
                return False
            if self._template("map/areaminigame.png") is None:
                self.log("A referência imags/map/areaminigame.png não foi encontrada; não consigo localizar o minigame na tela.", "error", name)
                return False
        if name == "loot":
            loot = self.config["loot"]
            targets = loot.get("targets") or []
            interval = float(loot.get("check_interval", 5) or 0)
            if not targets:
                self.log("Selecione pelo menos um tile na grade de Loot.", "error", name)
                return False
            if any(len(loot.get(key) or []) != 2 for key in ("center", "north", "east")):
                self.log("Marque o centro, o tile norte e o tile leste na aba Loot.", "error", name)
                return False
            if (len(loot.get("window_sample_region") or []) != 4
                    or len(loot.get("anchor_offset") or []) != 2
                    or len(loot.get("first_slot_offset") or []) != 4
                    or self._template("loot/window_anchor.png") is None
                    or self._template("loot/empty_slot.png") is None):
                self.log("Configure a janela de loot, a âncora, o primeiro slot e a referência do slot vazio na aba Loot.", "error", name)
                return False
            if not 0.5 <= interval <= 3600:
                self.log("O intervalo de verificação do Loot deve ficar entre 0,5 e 3600 segundos.", "error", name)
                return False
        if name == "macro":
            macro_name = str(self.config["macro"].get("name") or "").strip()
            if not macro_name or macro_name not in self.list_macros():
                self.log("Selecione uma rota gravada antes de ligar este módulo.", "error", name)
                return False
        if name == "macro" and self.config["battle"].get("long_battle_enabled"):
            delay = float(self.config["battle"].get("long_battle_after", 0) or 0)
            battle = self.config["battle"]
            key = str(battle.get("long_battle_key") or "").strip().lower()
            repeats = int(battle.get("long_battle_repeats", 1))
            if not 1 <= delay <= 3600:
                self.log("Configure a espera sem encontro entre 1 e 3600 segundos.", "error", name)
                return False
            if key not in MACRO_KEYS.values() or not 1 <= repeats <= 20:
                self.log("Configure uma tecla extra válida e de 1 a 20 repetições na aba Batalha.", "error", name)
                return False
        if name == "switch":
            slots = [s for s in self.config["switch"]["slots"] if s.get("point") and len(s["point"]) == 2]
            if len(slots) < 2:
                self.log("Configure pelo menos duas posições em Ações > Troca de Pokémon.", "error", name)
                return False
            if not 1 <= float(self.config["switch"].get("interval", 60)) <= 3600:
                self.log("O intervalo de troca deve ficar entre 1 e 3600 segundos.", "error", name)
                return False
        if name == "combat":
            mode = self.config["switch"].get("mode", "battle")
            if mode not in ("battle", "battle_switch", "switch"):
                self.log("Selecione um modo de combate válido em Ações > Troca de Pokémon.", "error", name)
                return False
            if mode in ("battle_switch", "switch"):
                slots = [s for s in self.config["switch"]["slots"] if s.get("point") and len(s["point"]) == 2]
                if len(slots) < 2:
                    self.log("Marque pelo menos duas posições para usar um modo com troca.", "error", name)
                    return False
                if not 1 <= float(self.config["switch"].get("interval", 60)) <= 3600:
                    self.log("O intervalo de troca deve ficar entre 1 e 3600 segundos.", "error", name)
                    return False
        if not self._ensure_interception():
            return False

        stop = threading.Event()
        target = getattr(self, f"_run_{name}")

        def runner():
            self.stats.module_on(name)
            self.log("Ligado.", "success", name)
            try:
                target(stop)
            except Exception as e:
                self.alert(f"Erro: {e}", "error", name)
            finally:
                if name == "battle":
                    self._set_macro_pause("battle", False)
                if name == "cavebot":
                    self._cavebot_ignored_battle_active = False
                if name == "pairing":
                    self._set_macro_pause("pairing", False)
                    self._resume_after_pairing()
                last_status = self._status.get(name, "")
                self.stats.module_off(name)
                self.log("Desligado.", "info", name)
                stop_reason = self._stop_reasons.pop(name, None)
                if stop_reason:
                    self._status[name] = f"{stop_reason}; \u00faltimo estado: {last_status}"
                elif last_status not in ("Ligado.", "Rodando", "Desligado."):
                    self._status[name] = f"Encerrado: {last_status}"
                else:
                    self._status[name] = "Sem atividade"

        self._stops[name] = stop
        t = threading.Thread(target=runner, daemon=True, name=name)
        self._threads[name] = t
        t.start()
        return True

    def stop(self, name, reason="Desligado manualmente"):
        ev = self._stops.get(name)
        if ev:
            self._stop_reasons[name] = reason
            ev.set()
        if name == "macro":
            self.stop_macro_playback()

    def stop_all(self):
        for name in MODULES:
            self.stop(name, reason="Parado por Parar tudo")
        self.stop_macro_recording()
        self.stop_macro_playback()

    def toggle(self, name):
        if self.is_running(name):
            self.stop(name)
        else:
            self.start(name)

    def _ensure_interception(self):
        with self._ic_lock:
            if self._ic_ready:
                return True
            try:
                ic.auto_capture_devices(keyboard=True, mouse=True, verbose=False)
                self._ic_ready = True
                return True
            except Exception as e:
                self.log(f"Falha ao iniciar o Interception (driver instalado?): {e}", "error")
                return False

    def _hotkey_watcher(self):
        user32 = getattr(ctypes, "windll", None) and ctypes.windll.user32
        if not user32:
            return
        down_before = set()
        while True:
            stop_vk = VK_CODES.get(str(self.config.get("stop_hotkey", "F12")).upper(), 0x7B)
            pick_vk = VK_CODES[PICK_HOTKEY]
            module_vks = {VK_CODES[k.upper()]: m for m, k in self.config["hotkeys"].items()
                          if k and k.upper() in VK_CODES and VK_CODES[k.upper()] not in (stop_vk, pick_vk)}
            watched = (stop_vk, pick_vk, VK_ESCAPE, *module_vks)
            down = {vk for vk in watched if user32.GetAsyncKeyState(vk) & 0x8000}
            pressed = down - down_before
            down_before = down
            if stop_vk in pressed and self.any_running():
                self.stop_all()
                self.log(f"{self.config.get('stop_hotkey')} pressionado: parando tudo.", "error")
            if pick_vk in pressed and self._pick:
                self._pick_press()
            if VK_ESCAPE in pressed and self._pick and self._pick["result"] is None:
                self.cancel_pick()
            for vk, module in module_vks.items():
                if vk in pressed:
                    threading.Thread(target=self.toggle, args=(module,), daemon=True).start()
            time.sleep(0.05)

    # ---------- ações básicas ----------
    def _battle_empty(self, trigger_long_action=True):
        now = time.monotonic()
        cached = None
        with self._battle_detector_lock:
            if now - self._battle_detector_checked_at < 0.2:
                cached = self._battle_detector_cache
            else:
                self._battle_detector_checked_at = now
        if cached is not None:
            self._maybe_auto_target(not cached)
            if trigger_long_action:
                self._maybe_long_battle_action(not cached)
            return cached

        template = self._template("battle/batalha_vazia.png")
        empty = False
        if template is not None:
            region = self.config["battle"].get("empty_region") or []
            screenshot, _ = self._grab(self._abs_region(region) if len(region) == 4 else None)
            score, _ = self._battle_empty_score(template, screenshot)
            empty = score >= self._battle_empty_threshold()

        with self._battle_detector_lock:
            if empty != self._battle_detector_state:
                if empty == self._battle_detector_candidate:
                    self._battle_detector_candidate_count += 1
                else:
                    self._battle_detector_candidate = empty
                    self._battle_detector_candidate_count = 1
                if self._battle_detector_candidate_count >= 2:
                    self._battle_detector_state = empty
                    self._battle_detector_candidate = None
                    self._battle_detector_candidate_count = 0
            else:
                self._battle_detector_candidate = None
                self._battle_detector_candidate_count = 0
            self._battle_detector_cache = self._battle_detector_state
            empty = self._battle_detector_cache
        if trigger_long_action:
            self._maybe_long_battle_action(not empty)
        self._maybe_auto_target(not empty)
        return empty

    def _maybe_auto_target(self, active):
        battle = self.config["battle"]
        if not battle.get("auto_target_enabled") or not active:
            with self._auto_target_lock:
                self._auto_target_last_pressed_at = 0.0
                self._auto_target_active = False
            return
        now = time.monotonic()
        interval = max(0.1, float(battle.get("interval", 0.5) or 0.5))
        with self._auto_target_lock:
            newly_active = not self._auto_target_active
            self._auto_target_active = True
            if now - self._auto_target_last_pressed_at < interval:
                return
            self._auto_target_last_pressed_at = now
        key = str(battle.get("auto_target_key") or "").strip().lower()
        if key not in MACRO_KEYS.values():
            self.log("Configure uma tecla válida para o auto target na aba Batalha.", "error", "battle")
            return
        ic.press(key)
        if newly_active:
            self.log(f"Inimigo detectado; auto target pressionou {key.upper()}.", "success", "battle")

    def _maybe_long_battle_action(self, active):
        config = self.config["battle"]
        after = float(config.get("long_battle_after", 0) or 0)
        if not active or not config.get("long_battle_enabled") or after <= 0:
            with self._long_battle_lock:
                self._long_battle_started_at = None
                self._long_battle_triggered = False
            return
        now = time.monotonic()
        with self._long_battle_lock:
            if self._long_battle_started_at is None:
                self._long_battle_started_at = now
                self._long_battle_triggered = False
            if self._long_battle_triggered or now - self._long_battle_started_at < after:
                return
            self._long_battle_triggered = True
            key = str(config.get("long_battle_key") or "").strip().lower()
            repeats = max(1, min(20, int(config.get("long_battle_repeats", 1))))
        if key not in MACRO_KEYS.values():
            self.log(f"Tecla inválida para batalha longa: {key or '(vazia)'}.", "error", "battle")
            return
        for index in range(repeats):
            ic.press(key)
            if index + 1 < repeats:
                time.sleep(0.12)
        self.log(f"Batalha longa: apertei {key.upper()} {repeats} vez(es).", "success", "battle")

    def _battle_empty_threshold(self):
        configured = float(self.config["battle"].get("confidence", 0.9))
        # O painel varia alguns pixels de uma captura para outra; não use o
        # limite alto de 0.9 como único critério para declarar inimigo ativo.
        return min(0.78, max(0.55, configured))

    @staticmethod
    def _battle_empty_score(template, screenshot):
        template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
        screenshot_gray = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)
        color_score, color_loc = BotEngine._match(template, screenshot)
        gray_score, gray_loc = BotEngine._match(template_gray, screenshot_gray)
        return (color_score, color_loc) if color_score >= gray_score else (gray_score, gray_loc)

    def _set_macro_pause(self, reason, paused):
        category = "minigame" if reason == "pairing" else "capture" if str(reason).startswith("capture:") else "target" if str(reason).startswith("battle_target") else "battle" if "battle" in str(reason) else None
        if paused and category in ("battle", "target") and self._macro_playing:
            skip_after = float(self._macro_play_options.get("skip_battle_after", 0) or 0)
            started_at = self._macro_battle_started_at
            if skip_after > 0 and started_at is not None and time.monotonic() - started_at >= skip_after:
                paused = False
        with self._macro_pause_lock:
            category_reasons = self._macro_pause_reasons_by_category.get(category) if category else None
            was_paused = bool(category_reasons) if category_reasons is not None else False
            if paused:
                self._macro_pause_reasons.add(reason)
                if category_reasons is not None:
                    category_reasons.add(reason)
            else:
                self._macro_pause_reasons.discard(reason)
                if category_reasons is not None:
                    category_reasons.discard(reason)
            if category_reasons is not None and category_reasons:
                self._macro_resume_after[category] = 0.0
            elif category_reasons is not None and was_paused:
                self._macro_resume_after[category] = time.monotonic() + self._macro_play_options[f"delay_{category}"]
            if self._macro_pause_reasons:
                self._macro_pause.set()
            else:
                self._macro_pause.clear()

    def _click(self, x, y):
        """Move o mouse e clica pelo driver. Não usa ic.click(x, y), que depende do
        _utils interno do interception e quebra quando há outro pacote por cima."""
        with self._capture_order_lock:
            with self._mouse_action_lock:
                pg.moveTo(x, y)
                time.sleep(0.15)
                ic.click()

    def _right_click(self, x, y, stop=None):
        """Clique direito serializado; o loot cede imediatamente ao minigame."""
        while True:
            if stop is not None and not self._wait_ready(stop, "loot"):
                return False
            pause_requested = False
            with self._capture_order_lock:
                with self._mouse_action_lock:
                    if stop is not None and self._pairing_pause.is_set():
                        pause_requested = True
                    else:
                        pg.moveTo(x, y)
                        time.sleep(0.12)
                        if stop is not None and self._pairing_pause.is_set():
                            pause_requested = True
                        else:
                            ic.right_click()
                            return True
            if pause_requested and stop is not None and not self._wait_ready(stop, "loot"):
                return False

    def _ball_key(self, pokemon=None):
        overrides = self.config["capture"].get("keys", {})
        key = str(overrides.get(pokemon) or "").strip().lower() if pokemon else ""
        if key:
            return key
        key = str(self.config["capture"].get("key") or "").strip().lower()
        return key or "1"

    def _attack(self):
        for key in self.config["battle"]["attack_keys"]:
            ic.press(key)

    def _try_capture_once(self, module):
        """Uma passada pelos alvos com um único print da tela. Retorna True se clicou em algum."""
        c = self.config["capture"]
        img, offset = self._grab(self._abs_region(c["region"]))
        caught = False
        for name in dict.fromkeys(c["targets"] + c["alert_on"]):
            pos = self._locate(f"captura/{name}", c["confidence"], img=img, offset=offset)
            if not pos:
                continue
            poke = Path(name).stem
            if name in c["alert_on"] and time.time() - self._alerted.get(name, 0) > 60:
                self._alerted[name] = time.time()
                self.alert(f"{poke} apareceu na tela!", "success", module)
            if name in c["targets"]:
                self.log(f"{poke} encontrado, jogando pokébola.", "success", module)
                reason = f"capture:{module}:{name}"
                self._set_macro_pause(reason, True)
                try:
                    with self._capture_order_lock:
                        ic.press(self._ball_key(name))
                        time.sleep(0.1)
                        self._click(pos.x, pos.y)
                finally:
                    self._set_macro_pause(reason, False)
                self.stats.ball(poke, module)
                caught = True
        return caught

    # ---------- módulos ----------
    def _run_switch(self, stop):
        config = self.config["switch"]
        interval = float(config.get("interval", 60))
        slots = [s for s in config["slots"] if s.get("point") and len(s["point"]) == 2]
        if len(slots) < 2:
            self.log("Configure pelo menos duas posições para iniciar a troca automática.", "error", "switch")
            return
        index = 0
        self.log(f"Troca automática ligada: intervalo de {interval:g} s.", "info", "switch")
        while self._wait_ready(stop, "switch"):
            slots = [s for s in self.config["switch"]["slots"] if s.get("point") and len(s["point"]) == 2]
            if len(slots) < 2:
                self.log("Restam menos de duas posições marcadas; troca automática encerrada.", "error", "switch")
                return
            slot = slots[index % len(slots)]
            x, y = self._abs_point(slot["point"])
            self._click(x, y)
            self.log(f"Trocado para {slot['name']}.", "success", "switch")
            index += 1
            if stop.wait(interval):
                return

    def _run_combat(self, stop):
        mode = self.config["switch"].get("mode", "battle")
        slots = [s for s in self.config["switch"]["slots"] if s.get("point") and len(s["point"]) == 2]
        switch_interval = float(self.config["switch"].get("interval", 60))
        battle_interval = float(self.config["battle"].get("interval", 0.5))
        index = 0
        next_switch = 0.0
        labels = {"battle": "somente batalha", "battle_switch": "batalha e troca", "switch": "apenas troca"}
        self.log(f"Modo Combate: {labels[mode]}.", "info", "combat")
        try:
            while self._wait_ready(stop, "combat"):
                if mode == "switch":
                    # Não ataca neste modo, mas detecta a luta para pausar macros.
                    battle_active = not self._battle_empty()
                    switch_active = True
                    self._set_macro_pause("combat_battle", battle_active)
                else:
                    battle_active = not self._battle_empty()
                    switch_active = mode == "battle_switch" and battle_active
                    self._set_macro_pause("combat_battle", battle_active)

                if battle_active:
                    self._attack()

                if switch_active or mode == "switch":
                    slots = [s for s in self.config["switch"]["slots"] if s.get("point") and len(s["point"]) == 2]
                    if len(slots) < 2:
                        self.log("Restam menos de duas posições marcadas; encerrei o modo Combate.", "error", "combat")
                        return
                    now = time.monotonic()
                    if now >= next_switch:
                        slot = slots[index % len(slots)]
                        x, y = self._abs_point(slot["point"])
                        self._click(x, y)
                        self.log(f"Troca do ciclo: {slot['name']}.", "success", "combat")
                        index += 1
                        next_switch = now + switch_interval
                elif mode == "battle_switch":
                    # Próxima batalha inicia um ciclo novo imediatamente.
                    next_switch = 0.0

                delay = switch_interval if mode == "switch" else battle_interval
                if stop.wait(max(0.05, delay)):
                    return
        finally:
            self._set_macro_pause("combat_battle", False)

    def _pairing_features(self, crops):
        """Embedding ResNet18 multiângulo e em escala de cinza para os seis ícones."""
        if self._pairing_model is None:
            try:
                import torch
                import torchvision.models as models
                from PIL import Image
            except Exception as e:
                raise RuntimeError(f"O pareamento precisa de torch, torchvision e Pillow: {e}") from e
            try:
                weights = models.ResNet18_Weights.DEFAULT
                model = models.resnet18(weights=weights)
                preprocess = weights.transforms()
            except AttributeError:
                import torchvision.transforms as T
                model = models.resnet18(pretrained=True)
                preprocess = T.Compose([T.Resize(256), T.CenterCrop(224), T.ToTensor(),
                                        T.Normalize(mean=[0.485, 0.456, 0.406],
                                                    std=[0.229, 0.224, 0.225])])
            self._pairing_device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            self._pairing_model = torch.nn.Sequential(*list(model.children())[:-1]).to(self._pairing_device).eval()
            self._pairing_preprocess = preprocess
            self._pairing_image_cls = Image

        import torch
        tensors = []
        for crop in crops:
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            gray = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
            for source in (crop, gray):
                for angle in (0, 90, 180, 270):
                    if angle:
                        source_rotated = cv2.rotate(source, {90: cv2.ROTATE_90_CLOCKWISE,
                                                             180: cv2.ROTATE_180,
                                                             270: cv2.ROTATE_90_COUNTERCLOCKWISE}[angle])
                    else:
                        source_rotated = source
                    rgb = cv2.cvtColor(source_rotated, cv2.COLOR_BGR2RGB)
                    tensors.append(self._pairing_preprocess(self._pairing_image_cls.fromarray(rgb)))
        batch = torch.stack(tensors).to(self._pairing_device)
        with torch.inference_mode():
            vectors = self._pairing_model(batch).flatten(1)
            vectors = torch.nn.functional.normalize(vectors, p=2, dim=1).reshape(len(crops), 8, -1).mean(dim=1)
            vectors = torch.nn.functional.normalize(vectors, p=2, dim=1)
        return vectors.cpu().numpy()

    def _resolve_pairs(self, img):
        """Divide a captura em seis slots, gera similaridades e pareia globalmente."""
        from scipy.optimize import linear_sum_assignment
        h, w = img.shape[:2]
        if h < 20 or w < 20:
            raise ValueError("A área do minigame ficou pequena demais; marque a região novamente.")
        # Proporções iniciais correspondentes ao layout 3×2 do script de captcha.
        x_ranges = ((0.05, 0.33), (0.36, 0.64), (0.67, 0.95))
        y_ranges = ((0.22, 0.48), (0.52, 0.78))
        crops, centers = [], []
        for y0, y1 in y_ranges:
            for x0, x1 in x_ranges:
                left, right = int(w * x0), int(w * x1)
                top, bottom = int(h * y0), int(h * y1)
                crop = img[top:bottom, left:right]
                if crop.size == 0:
                    raise ValueError("Não consegui recortar os ícones. Ajuste a região do minigame.")
                crops.append(crop)
                centers.append(((left + right) // 2, (top + bottom) // 2))
        vectors = self._pairing_features(crops)
        similarities = vectors[:3] @ vectors[3:].T
        rows, cols = linear_sum_assignment(1.0 - similarities)
        return list(zip(rows.tolist(), cols.tolist())), similarities, centers

    def _minigame_present(self, img):
        """Busca o minigame na imagem toda e retorna sua caixa atual (x, y, largura, altura)."""
        reference = self._template("map/areaminigame.png")
        if reference is None or img is None or img.size == 0:
            return False, 0.0, None
        rh, rw = reference.shape[:2]
        # O título e a barra inferior são estáveis, ao contrário das seis figuras.
        title = cv2.cvtColor(reference[0:max(12, int(rh * 0.13)), int(rw * 0.025):int(rw * 0.975)],
                             cv2.COLOR_BGR2GRAY)
        footer = cv2.cvtColor(reference[int(rh * 0.84):rh, int(rw * 0.025):int(rw * 0.975)],
                              cv2.COLOR_BGR2GRAY)
        screen = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        best = (0.0, None)

        def top_matches(match_map, width, height, limit=12, threshold=0.4):
            work = match_map.copy()
            matches = []
            for _ in range(limit):
                _, value, _, location = cv2.minMaxLoc(work)
                if value < threshold:
                    break
                matches.append((float(value), location))
                x, y = location
                x0, x1 = max(0, x - width // 2), min(work.shape[1], x + width // 2 + 1)
                y0, y1 = max(0, y - height // 2), min(work.shape[0], y + height // 2 + 1)
                work[y0:y1, x0:x1] = -1
            return matches

        # A escala permite pequenas diferenças de resolução no cliente do jogo.
        for scale in (0.65, 0.75, 0.85, 0.95, 1.0, 1.1, 1.2, 1.35, 1.5):
            tw, th = int(title.shape[1] * scale), int(title.shape[0] * scale)
            fw, fh = int(footer.shape[1] * scale), int(footer.shape[0] * scale)
            if min(tw, th, fw, fh) < 4 or tw > screen.shape[1] or fw > screen.shape[1] \
                    or th > screen.shape[0] or fh > screen.shape[0]:
                continue
            title_tpl = cv2.resize(title, (tw, th), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
            footer_tpl = cv2.resize(footer, (fw, fh), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
            title_map = cv2.matchTemplate(screen, title_tpl, cv2.TM_CCOEFF_NORMED)
            footer_map = cv2.matchTemplate(screen, footer_tpl, cv2.TM_CCOEFF_NORMED)
            titles = top_matches(title_map, tw, th)
            footers = top_matches(footer_map, fw, fh)
            expected_gap = int((rh * 0.84) * scale)
            for title_score, title_loc in titles:
                for footer_score, footer_loc in footers:
                    actual_gap = footer_loc[1] - title_loc[1]
                    same_panel = (
                        abs(actual_gap - expected_gap) <= max(7, int(expected_gap * 0.12))
                        and abs((footer_loc[0] + fw / 2) - (title_loc[0] + tw / 2))
                        <= max(18, tw * 0.12)
                    )
                    score = (title_score + footer_score) / 2 if same_panel else 0.0
                    if score <= best[0]:
                        continue
                    x = int(round(title_loc[0] - rw * 0.025 * scale))
                    y = int(title_loc[1])
                    width, height = int(round(rw * scale)), int(round(rh * scale))
                    if x < 0 or y < 0 or x + width > img.shape[1] or y + height > img.shape[0]:
                        continue
                    best = (score, [x, y, width, height])
        return best[0] >= 0.58, best[0], best[1]

    def _pause_for_pairing(self, pairing_stop):
        """Wait for other modules to reach a safe point before puzzle clicks."""
        self._pairing_pause.set()
        with self._macro_action_lock:
            pass
        # Aguarda os cliques iniciados antes da pausa do minigame terminarem.
        with self._capture_order_lock:
            with self._mouse_action_lock:
                pass
        deadline = time.monotonic() + 60
        while not pairing_stop.is_set():
            active = {name for name in MODULES if name not in ("pairing", "macro") and self.is_running(name)}
            with self._pause_state_lock:
                acknowledged = self._paused_modules.copy()
            pending = active - acknowledged
            if not pending:
                if active:
                    self.log("Outros módulos pausados para resolver o minigame.", "info", "pairing")
                return True
            if time.monotonic() >= deadline:
                names = ", ".join(MODULES[name] for name in sorted(pending))
                self.log(f"Não consegui pausar estes módulos: {names}. Nenhum clique do minigame foi enviado.",
                         "error", "pairing")
                self._pairing_pause.clear()
                return False
            pairing_stop.wait(0.1)
        self._pairing_pause.clear()
        return False

    def _pause_for_loot(self, loot_stop):
        """Pausa outros workers e a rota enquanto o loot usa mouse e tela."""
        self._loot_pause.set()
        self._set_macro_pause("loot", True)
        with self._macro_action_lock:
            pass
        deadline = time.monotonic() + 15
        while not loot_stop.is_set():
            # O minigame tem prioridade e pode assumir a pausa enquanto aguardamos.
            if self._pairing_pause.is_set():
                self._wait_ready(loot_stop, "loot")
                loot_stop.wait(0.1)
                continue
            active = {name for name in MODULES if name not in ("loot", "pairing", "macro", "battle", "heal") and self.is_running(name)}
            with self._pause_state_lock:
                acknowledged = self._paused_modules.copy()
            pending = active - acknowledged
            if not pending:
                return True
            if time.monotonic() >= deadline:
                names = ", ".join(MODULES[name] for name in sorted(pending))
                self.log(f"Não consegui pausar estes módulos para coletar o loot: {names}. Nenhum clique foi enviado.", "error", "loot")
                self._resume_after_loot()
                return False
            loot_stop.wait(0.1)
        self._resume_after_loot()
        return False

    def _resume_after_loot(self):
        self._loot_pause.clear()
        self._set_macro_pause("loot", False)

    def _resume_after_pairing(self):
        self._pairing_pause.clear()

    def _wait_pairing_panel_close(self, stop, region=None):
        """Mantém os módulos pausados até o painel sumir da tela toda."""
        error_reported = False
        while not stop.is_set():
            if not self._wait_ready(stop, "pairing"):
                return False
            try:
                check, _ = self._grab()
                still_present, _, _ = self._minigame_present(check)
                if not still_present:
                    return True
                error_reported = False
            except Exception as error:
                if not error_reported:
                    self.log(f"Falha temporária ao verificar a tela do minigame: {error}; tentando novamente.", "warn", "pairing")
                    error_reported = True
            stop.wait(0.5)
        return False

    def _run_pairing(self, stop):
        self.log("Procurando o minigame em toda a tela...", "info", "pairing")
        while self._wait_ready(stop, "pairing"):
            pause_active = False
            input_started = False
            try:
                screen, _ = self._grab()
                present, _, panel = self._minigame_present(screen)
                if not present:
                    stop.wait(0.5)
                    continue
                x, y, width, height = panel
                region = panel
                img = screen[y:y + height, x:x + width].copy()
                offset = (x, y)

                self.log("Minigame detectado; calculando os pares...", "info", "pairing")
                # Sinaliza a prioridade antes de esperar qualquer ação em andamento.
                self._pairing_pause.set()
                pause_active = True
                with self._macro_action_lock:
                    self._set_macro_pause("pairing", True)
                if not self._pause_for_pairing(stop):
                    if stop.is_set():
                        return
                    self._set_macro_pause("pairing", False)
                    pause_active = False
                    stop.wait(0.5)
                    continue

                # Take a fresh image after all modules have reached their pause point.
                screen, _ = self._grab()
                present, _, panel = self._minigame_present(screen)
                if not present:
                    self.log("O minigame fechou enquanto os outros módulos eram pausados; nenhum clique foi enviado.", "warn", "pairing")
                    self._resume_after_pairing()
                    self._set_macro_pause("pairing", False)
                    pause_active = False
                    stop.wait(0.5)
                    continue
                x, y, width, height = panel
                region = panel
                img = screen[y:y + height, x:x + width].copy()
                offset = (x, y)

                pairs, similarities, centers = self._resolve_pairs(img)
                for top_idx, bottom_idx in pairs:
                    if stop.is_set() or not self._wait_ready(stop, "pairing"):
                        return
                    top = centers[top_idx]
                    bottom = centers[3 + bottom_idx]
                    input_started = True
                    self._click(offset[0] + top[0], offset[1] + top[1])
                    time.sleep(0.12)
                    self._click(offset[0] + bottom[0], offset[1] + bottom[1])
                    self.log(f"Pareando ícone superior {top_idx + 1} com ícone inferior {bottom_idx + 1} "
                             f"(similaridade {similarities[top_idx, bottom_idx]:.2f}).", "success", "pairing")
                    if stop.wait(0.25):
                        return

                if not self._wait_ready(stop, "pairing"):
                    return
                confirm_x = offset[0] + int(img.shape[1] * 0.75)
                confirm_y = offset[1] + int(img.shape[0] * 0.91)
                input_started = True
                self._click(confirm_x, confirm_y)
                self.log("Pares selecionados; cliquei em Confirmar. Aguardando o painel fechar...", "info", "pairing")

                deadline = time.monotonic() + 5.0
                closed = False
                while not stop.is_set() and time.monotonic() < deadline:
                    if not self._wait_ready(stop, "pairing"):
                        return
                    check, _ = self._grab()
                    still_present, _, _ = self._minigame_present(check)
                    if not still_present:
                        closed = True
                        self.log("Minigame fechado; resolução concluída.", "success", "pairing")
                        break
                    stop.wait(0.35)

                if not closed and not stop.is_set():
                    self.log("O painel ainda está visível após a confirmação; mantendo os módulos pausados sem repetir cliques.",
                             "warn", "pairing")
                    closed = self._wait_pairing_panel_close(stop)
                    if closed:
                        self.log("Painel do minigame fechado; retomando os módulos.", "success", "pairing")

                if stop.is_set():
                    return
                self._resume_after_pairing()
                self._set_macro_pause("pairing", False)
                pause_active = False
                stop.wait(0.7)

            except Exception as error:
                self.log(f"Erro no minigame; o módulo continua ativo: {error}", "error", "pairing")
                self._status["pairing"] = "Recuperando após erro"
                try:
                    if pause_active and input_started:
                        self.log("Uma ação pode ter sido enviada; mantendo os outros módulos pausados até o painel fechar.",
                                 "warn", "pairing")
                        closed = self._wait_pairing_panel_close(stop)
                        if not closed and stop.is_set():
                            return
                except Exception as recovery_error:
                    self.log(f"Falha ao verificar a recuperação do minigame: {recovery_error}; vou tentar novamente em instantes.", "warn", "pairing")
                finally:
                    self._resume_after_pairing()
                    self._set_macro_pause("pairing", False)
                if stop.is_set():
                    return
                self._status["pairing"] = "Rodando; tentando detectar o minigame novamente"
                stop.wait(1.0)
            finally:
                if pause_active:
                    self._resume_after_pairing()
                    self._set_macro_pause("pairing", False)
    def capture_loot_anchor(self, region):
        loot = self.config["loot"]
        window = loot.get("window_sample_region") or []
        if len(window) != 4 or len(region or []) != 4:
            raise ValueError("Marque primeiro a janela de loot inteira e depois uma âncora visual dentro dela.")
        if min(window[2:]) < 20 or min(region[2:]) < 6:
            raise ValueError("A janela e a âncora precisam ter uma área válida.")
        if (region[0] < window[0] or region[1] < window[1]
                or region[0] + region[2] > window[0] + window[2]
                or region[1] + region[3] > window[1] + window[3]):
            raise ValueError("A âncora precisa ficar dentro da janela de loot marcada.")
        image, _ = self._grab(self._abs_region(region))
        path = self.img / "loot" / "window_anchor.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not _write_png(path, image):
            raise RuntimeError("Não consegui salvar a referência visual da janela de loot.")
        loot["anchor_region"] = [int(value) for value in region]
        loot["anchor_offset"] = [int(region[0] - window[0]), int(region[1] - window[1])]
        self._templates.pop("loot/window_anchor.png", None)
        self.save_config(self.config, quiet=True)
        self.log("Âncora visual da janela de loot salva.", "success", "loot")
        return {"anchor_region": loot["anchor_region"], "anchor_offset": loot["anchor_offset"]}

    def capture_loot_empty_slot(self):
        """Salva a aparência do primeiro slot vazio para identificar itens restantes."""
        found = self.locate_loot_window()
        if not found or not found.get("first_slot_region"):
            raise ValueError("Abra uma janela de loot sem itens e calibre o primeiro slot antes de capturar a referência.")
        image, _ = self._grab(found["first_slot_region"])
        if image.size == 0:
            raise ValueError("O primeiro slot calibrado ficou fora da tela.")
        path = self.img / "loot" / "empty_slot.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not _write_png(path, image):
            raise RuntimeError("Não consegui salvar a referência do slot vazio.")
        self._templates.pop("loot/empty_slot.png", None)
        self.log("Referência do primeiro slot vazio salva.", "success", "loot")
        return True

    def _loot_slot_has_item(self, found):
        region = found.get("first_slot_region")
        empty = self._template("loot/empty_slot.png")
        if not region or empty is None:
            raise ValueError("O primeiro slot ou a referência do slot vazio não foi calibrado.")
        current, _ = self._grab(region)
        if current.shape[:2] != empty.shape[:2]:
            current = cv2.resize(current, (empty.shape[1], empty.shape[0]), interpolation=cv2.INTER_AREA)
        difference = cv2.absdiff(current, empty)
        # Ignore 2px de moldura: a borda pode piscar mesmo quando o slot esta vazio.
        if difference.shape[0] > 4 and difference.shape[1] > 4:
            difference = difference[2:-2, 2:-2]
        changed = np.max(difference, axis=2) > 32
        return float(np.mean(changed)) >= 0.055

    def locate_loot_window(self, confidence=0.84):
        loot = self.config["loot"]
        window = loot.get("window_sample_region") or []
        anchor_offset = loot.get("anchor_offset") or []
        if len(window) != 4 or len(anchor_offset) != 2:
            raise ValueError("Calibre a janela inteira e a âncora visual na aba Loot.")
        template = self._template("loot/window_anchor.png")
        if template is None:
            raise ValueError("A referência da âncora da janela de loot não foi capturada.")
        screen, _ = self._grab()
        template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
        screen_gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
        score, (ax, ay) = self._match(template_gray, screen_gray)
        if score < confidence:
            return None
        x = int(ax - anchor_offset[0])
        y = int(ay - anchor_offset[1])
        width, height = int(window[2]), int(window[3])
        screen_h, screen_w = screen.shape[:2]
        if x < 0 or y < 0 or x + width > screen_w or y + height > screen_h:
            return None
        found = {"region": [x, y, width, height], "anchor_score": score}
        first_slot = loot.get("first_slot_offset") or []
        if len(first_slot) == 4:
            found["first_slot_region"] = [x + int(first_slot[0]), y + int(first_slot[1]),
                                          int(first_slot[2]), int(first_slot[3])]
        return found

    def _run_loot(self, stop):
        self.log("Loot ativo; verificando periodicamente os tiles selecionados.", "info", "loot")
        directions = {
            "nw": (-1, 1), "n": (0, 1), "ne": (1, 1),
            "w": (-1, 0), "e": (1, 0),
            "sw": (-1, -1), "s": (0, -1), "se": (1, -1),
        }
        last_sweep = 0.0
        while self._wait_ready(stop, "loot"):
            interval = float(self.config["loot"].get("check_interval", 5) or 5)
            remaining = interval - (time.monotonic() - last_sweep)
            if remaining > 0:
                self._status["loot"] = f"Aguardando a próxima verificação ({remaining:.0f} s)"
                if stop.wait(min(remaining, 0.25)):
                    return
                continue
            if not self._pause_for_loot(stop):
                if stop.is_set():
                    return
                stop.wait(1)
                continue
            last_sweep = time.monotonic()
            try:
                loot = self.config["loot"]
                center = loot["center"]
                north = loot["north"]
                east = loot["east"]
                nvec = (int(north[0]) - int(center[0]), int(north[1]) - int(center[1]))
                evec = (int(east[0]) - int(center[0]), int(east[1]) - int(center[1]))
                selected = [key for key in loot.get("targets", []) if key in directions]
                abort_sweep = False
                for direction in selected:
                    if not self._wait_ready(stop, "loot"):
                        return
                    dx, dy = directions[direction]
                    point = (int(center[0]) + dx * evec[0] + dy * nvec[0],
                             int(center[1]) + dx * evec[1] + dy * nvec[1])
                    x, y = self._abs_point(point)
                    self._status["loot"] = f"Verificando tile {direction.upper()}"
                    if not self._right_click(x, y, stop=stop):
                        return
                    # A caixa pode permanecer aberta e atualizar o conteudo ao abrir outro Pokemon.
                    stop.wait(0.25)
                    found = None
                    deadline = time.monotonic() + 1.5
                    while not stop.is_set() and time.monotonic() < deadline:
                        if not self._wait_ready(stop, "loot"):
                            return
                        found = self.locate_loot_window()
                        if found:
                            break
                        stop.wait(0.15)
                    if not found:
                        continue
                    slot = found.get("first_slot_region")
                    if not slot:
                        self.log("Janela localizada sem o primeiro slot calibrado; interrompi a varredura por segurança.", "error", "loot")
                        break
                    self._status["loot"] = f"Loot no tile {direction.upper()}"
                    collected = 0
                    empty_checks = 0
                    while not stop.is_set() and collected < 50:
                        if not self._wait_ready(stop, "loot"):
                            return
                        found = self.locate_loot_window()
                        if not found:
                            break  # a janela fechou sozinha após a coleta
                        if not self._loot_slot_has_item(found):
                            # Aguarda a UI concluir a troca de conteudo antes de considerar
                            # este Pokemon sem loot; a janela pode continuar aberta.
                            empty_checks += 1
                            if empty_checks < 3:
                                stop.wait(0.2)
                                continue
                            # Não fecha a janela: o próximo clique atualiza a caixa existente.
                            break
                        empty_checks = 0
                        slot = found["first_slot_region"]
                        if not self._right_click(slot[0] + slot[2] // 2, slot[1] + slot[3] // 2, stop=stop):
                            return
                        collected += 1
                        stop.wait(0.18)
                    if collected >= 50:
                        self.log("Limite de 50 coletas nesta caixa atingido; parei para evitar loop de cliques.", "warn", "loot")
                        abort_sweep = True
                    if stop.is_set():
                        return
                    if abort_sweep:
                        break
            except Exception as error:
                self.log(f"Falha na varredura de loot: {error}", "error", "loot")
                self._status["loot"] = "Erro na varredura; tentando novamente"
                stop.wait(1)
            finally:
                self._resume_after_loot()
            self._status["loot"] = "Varredura concluída; aguardando o intervalo"

    def _run_battle(self, stop):
        last = None
        while self._wait_ready(stop, "battle"):
            empty = self._battle_empty()
            self._set_macro_pause("battle", not empty)
            if not empty:
                self._attack()
            if empty != last:
                self.log("Sem inimigos na batalha." if empty else "Inimigo detectado, atacando.", "info", "battle")
                # o cavebot já conta as próprias batalhas
                if empty and last is False and not self.is_running("cavebot"):
                    self.stats.kill("battle")
                last = empty
            stop.wait(self.config["battle"]["interval"])
        self._set_macro_pause("battle", False)

    def _run_capture(self, stop):
        self.log("Procurando Pokémon para capturar...", "info", "capture")
        while self._wait_ready(stop, "capture"):
            self._try_capture_once("capture")
            stop.wait(self.config["capture"]["interval"])

    def _run_heal(self, stop):
        last = 0.0
        next_buff = 0.0
        next_follow = 0.0
        next_order = 0.0
        self.log("Vigiando vida e acoes continuas configuradas.", "info", "heal")
        while self._wait_ready(stop, "heal"):
            c = self.config["heal"]
            now = time.monotonic()
            buff_key = str(c.get("buff_key") or "").strip().lower()
            if buff_key and now >= next_buff:
                ic.press(buff_key)
                next_buff = now + max(0.5, float(c.get("buff_interval", 30)))
                self.log(f"Buff continuo: apertei {buff_key.upper()}.", "info", "heal")
            follow_key = str(c.get("follow_key") or "").strip().lower()
            if c.get("follow_enabled") and follow_key in MACRO_KEYS.values() and now >= next_follow:
                ic.press(follow_key)
                next_follow = now + max(0.1, float(c.get("follow_interval", 5) or 5))
            order_key = str(c.get("order_key") or "").strip().lower()
            order_point = c.get("order_point") or []
            if (
                c.get("order_enabled")
                and order_key in MACRO_KEYS.values()
                and len(order_point) == 2
                and now >= next_order
            ):
                acquired = self._capture_order_lock.acquire(
                    blocking=True
                )
                if acquired:
                    try:
                        x, y = self._abs_point(order_point)
                        with self._mouse_action_lock:
                            ic.press(order_key)
                            time.sleep(0.1)
                            self._click(x, y)
                        next_order = time.monotonic() + max(0.1, float(c.get("order_interval", 5) or 5))
                        self._status["heal"] = f"Ordem de pesca enviada em {x}, {y}; proxima em {c.get('order_interval', 5)}s"
                    finally:
                        self._capture_order_lock.release()
            if len(c.get("pixel") or []) == 2 and len(c.get("color") or []) == 3:
                x, y = self._abs_point(c["pixel"])
                if list(self._pixel(x, y)) != list(c["color"]) and time.time() - last >= c["cooldown"]:
                    ic.press(c["key"])
                    last = time.time()
                    self.stats.heal()
                    self.log(f"Vida baixa, apertei {c['key'].upper()}.", "success", "heal")
            wait_interval = max(0.05, float(c.get("interval", 0.3) or 0.3))
            if c.get("follow_enabled"):
                wait_interval = min(wait_interval, max(0.05, float(c.get("follow_interval", 5) or 5)))
            if c.get("order_enabled"):
                wait_interval = min(wait_interval, max(0.05, float(c.get("order_interval", 5) or 5)))
            stop.wait(wait_interval)

    def _run_cavebot(self, stop):
        self._cavebot_ignored_battle_active = False
        misses = 0
        while not stop.is_set():
            c = self.config["cavebot"]
            route = self.route()
            if not route:
                self.log("Nenhum waypoint na rota.", "error", "cavebot")
                return
            found_any = False
            for wp, walk_time in route:
                if not self._wait_ready(stop, "cavebot"):
                    return
                if not self._fight_and_capture(stop):
                    return
                pos = self._locate(f"map/{wp}", c["confidence"], self._abs_region(c["map_region"]))
                if not pos:
                    continue
                found_any = True
                self.log(f"Indo para o ponto {Path(wp).stem}.", "info", "cavebot")
                prev = pg.position()
                self._click(pos.x, pos.y)
                engage_delay = max(0.0, float(c.get("engage_delay", 0)))
                if engage_delay and stop.wait(engage_delay):
                    return
                if not self._wait_cavebot_walk(stop, walk_time):
                    return
                pg.moveTo(prev)
                if not self._wait_ready(stop, "cavebot"):
                    return
                if not self._fight_and_capture(stop):
                    return
            if found_any:
                misses = 0
                continue
            misses += 1
            limit = int(self.config["safety"]["max_misses"])
            if limit and misses >= limit:
                self.alert(f"Cavebot travado: {misses} voltas sem achar nenhum ponto no minimapa. Parei.", "error", "cavebot")
                return
            self.log(f"Nenhum ponto visível no minimapa ({misses}/{limit}).", "warn", "cavebot")
            stop.wait(2)

    def _wait_cavebot_walk(self, stop, seconds):
        remaining = max(0.0, float(seconds))
        while remaining > 0 and not stop.is_set():
            if not self._wait_ready(stop, "cavebot"):
                return False
            if not self._battle_empty() or self._capture_target_visible():
                if not self._fight_and_capture(stop):
                    return False
                continue
            step = min(0.5, remaining)
            started = time.monotonic()
            if stop.wait(step):
                return False
            remaining = max(0.0, remaining - (time.monotonic() - started))
        return not stop.is_set()

    def _fight_and_capture(self, stop):
        battle_empty = self._battle_empty()
        if battle_empty:
            self._cavebot_ignored_battle_active = False
        battle_active = not battle_empty and not self._cavebot_ignored_battle_active
        capture_visible = self._capture_target_visible()
        if not battle_active and not capture_visible:
            return True
        target_reason = "battle_target:cavebot"
        target_was_present = self._battle_target_is_present()
        if target_was_present:
            self._set_macro_pause(target_reason, True)
        self._set_macro_pause("cavebot_battle", True)
        try:
            return self._fight_and_capture_active(stop, battle_active)
        finally:
            self._set_macro_pause("cavebot_battle", False)
            if target_was_present:
                self._set_macro_pause(target_reason, False)

    def _fight_and_capture_active(self, stop, battle_active=True):
        timeout = float(self.config["safety"]["fight_timeout"] or 0)
        skip_after = max(0.0, float(self.config["cavebot"].get("battle_skip_after", 0) or 0))
        started = time.monotonic()
        battle_skipped = False
        if battle_active:
            self.log("Inimigo detectado; aguardando a lista de batalha ficar vazia.", "info", "cavebot")
            while not stop.is_set() and not self._battle_empty():
                elapsed = time.monotonic() - started
                if skip_after and elapsed >= skip_after:
                    self._cavebot_ignored_battle_active = True
                    battle_skipped = True
                    self.log(f"Limite de {skip_after:g}s atingido; ignorando a batalha e verificando capturas.", "warn", "cavebot")
                    break
                if timeout and not skip_after and elapsed > timeout:
                    self.alert(f"A batalha passou de {int(timeout)} s sem terminar; parei a rota para não avançar durante o combate.", "warn", "cavebot")
                    return False
                if not self._wait_ready(stop, "cavebot"):
                    return False
                self._attack()
                stop.wait(max(0.1, float(self.config["battle"].get("interval", 0.5))))
            if stop.is_set():
                return False
            if not battle_skipped and self._battle_empty():
                self.stats.kill("cavebot")
                self._cavebot_ignored_battle_active = False
                self.log("Lista de batalha vazia; verificando capturas antes de continuar a rota.", "info", "cavebot")

        quiet_checks = 0
        capture_attempts = 0
        capture_started = time.monotonic()
        capture_interval = max(0.1, float(self.config["capture"].get("interval", 0.5)))
        while not stop.is_set():
            if not self._wait_ready(stop, "cavebot"):
                return False
            if self._capture_target_visible():
                quiet_checks = 0
                if capture_attempts < 5 and self._try_capture_once("cavebot"):
                    capture_attempts += 1
                elif timeout and time.monotonic() - capture_started > timeout:
                    self.alert("A captura continua pendente; parei a rota para não avançar sem resolver o alvo.", "warn", "cavebot")
                    return False
            else:
                quiet_checks += 1
                if quiet_checks >= 2:
                    self.log("Sem inimigos ou capturas pendentes; continuando a rota.", "info", "cavebot")
                    return True
            wait_until = time.monotonic() + capture_interval
            while not stop.is_set() and time.monotonic() < wait_until:
                if not self._wait_ready(stop, "cavebot"):
                    return False
                remaining = wait_until - time.monotonic()
                if stop.wait(min(0.1, max(0.01, remaining))):
                    return False
        return False

    def _capture_target_visible(self):
        capture = self.config["capture"]
        names = capture.get("targets") or []
        region = capture.get("region") or []
        if not names or len(region) != 4 or min(region[2:]) < 1:
            return False
        image, offset = self._grab(self._abs_region(region))
        confidence = float(capture.get("confidence", 0.75))
        return any(
            self._locate(f"captura/{name}", confidence, img=image, offset=offset)
            for name in dict.fromkeys(names)
        )

    # ---------- opacidade (mesma lógica de opacity.py) ----------
    def set_opacity(self, value):
        windows = self._find_game_windows()
        if not windows:
            self.log(f"Janela '{self.config['window_title']}' não encontrada.", "error")
            return False

        user32 = ctypes.windll.user32
        get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        get_long.restype = ctypes.c_ssize_t
        get_long.argtypes = [wintypes.HWND, ctypes.c_int]
        set_long.restype = ctypes.c_ssize_t
        set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.DWORD, wintypes.BYTE, wintypes.DWORD]

        value = max(1, min(255, int(value)))
        for w in windows:
            hwnd = w._hWnd
            set_long(hwnd, -20, get_long(hwnd, -20) | 0x00080000)  # WS_EX_LAYERED
            user32.SetLayeredWindowAttributes(hwnd, 0, value, 0x2)  # LWA_ALPHA
        self.log(f"Opacidade do jogo: {round(value / 255 * 100)}%.", "info")
        return True
    # ---------- macros ----------
    def configure_macro_module(self, name, loop=False):
        selected = str(name or "").strip()
        if selected and selected not in self.list_macros():
            raise ValueError("A macro selecionada não existe.")
        self.config["macro"]["name"] = selected
        self.config["macro"]["loop"] = bool(loop)
        self.save_config(self.config, quiet=True)
        return {"name": selected, "loop": bool(loop)}

    def _run_macro(self, stop):
        name = str(self.config["macro"].get("name") or "").strip()
        loop = bool(self.config["macro"].get("loop", False))
        self.play_macro(name, loop=loop)
        while not stop.wait(0.05) and self.macro_state()["playing"]:
            pass
        if stop.is_set():
            self.stop_macro_playback()
            with self._macro_lock:
                thread = self._macro_play_thread
            if thread and thread is not threading.current_thread():
                thread.join(timeout=3)

    @property
    def macros_dir(self):
        return self.root / "macros"

    @staticmethod
    def _default_macro_options():
        return {
            "pause_battle": True, "delay_battle": 0.0,
            "pause_capture": True, "delay_capture": 0.0,
            "pause_target": True, "delay_target": 0.0,
            "command_interval": 0.0,
            "skip_battle_after": 0.0,
        }

    @classmethod
    def _normalize_macro_options(cls, options=None, legacy_pause=True, legacy_delay=0):
        normalized = cls._default_macro_options()
        if isinstance(options, dict):
            for category in ("battle", "capture", "target"):
                normalized[f"pause_{category}"] = bool(options.get(f"pause_{category}", legacy_pause))
                delay = float(options.get(f"delay_{category}", legacy_delay))
                if not 0 <= delay <= 3600:
                    raise ValueError("Cada tempo de retomada deve ficar entre 0 e 3600 segundos.")
                normalized[f"delay_{category}"] = delay
            command_interval = float(options.get("command_interval", 0) or 0)
            if not 0 <= command_interval <= 3600:
                raise ValueError("O intervalo entre comandos deve ficar entre 0 e 3600 segundos.")
            normalized["command_interval"] = command_interval
            skip_battle_after = float(options.get("skip_battle_after", 0) or 0)
            if not 0 <= skip_battle_after <= 3600:
                raise ValueError("O limite para ignorar a batalha deve ficar entre 0 e 3600 segundos.")
            normalized["skip_battle_after"] = skip_battle_after
        else:
            legacy_delay = float(legacy_delay)
            if not 0 <= legacy_delay <= 3600:
                raise ValueError("Cada tempo de retomada deve ficar entre 0 e 3600 segundos.")
            for category in ("battle", "capture", "target"):
                normalized[f"pause_{category}"] = bool(legacy_pause)
                normalized[f"delay_{category}"] = legacy_delay
        return normalized

    def macro_state(self):
        with self._macro_pause_lock:
            now = time.monotonic()
            pause_reasons = [
                label
                for category, label in (("battle", "Batalha detectada"), ("capture", "Captura pendente"))
                if self._macro_play_options[f"pause_{category}"]
                and self._macro_pause_reasons_by_category[category]
            ]
            if self._macro_pause_reasons_by_category["minigame"]:
                pause_reasons.append("Minigame em execução")
            if "loot" in self._macro_pause_reasons:
                pause_reasons.append("Verificando loot")
            resume_remaining = max(
                (max(0.0, self._macro_resume_after[category] - now)
                 for category in ("battle", "capture", "target")
                 if self._macro_play_options[f"pause_{category}"]),
                default=0.0,
            )
            paused_by_category = {
                category: bool(self._macro_pause_reasons_by_category[category]) or self._macro_resume_after[category] > now
                for category in ("battle", "capture", "minigame", "target")
            }
        with self._battle_targets_lock:
            target_pause = bool(self._macro_play_options["pause_target"] and paused_by_category["target"])
        if target_pause:
            pause_reasons.append("Pokémon selecionado na lista de batalha")
        with self._macro_lock:
            elapsed = time.monotonic() - self._macro_record_started if self._macro_recording else 0.0
            return {
                "recording": self._macro_recording,
                "playing": self._macro_playing,
                "loop": self._macro_play_loop,
                "paused": self._macro_playing and (
                    any(
                        self._macro_play_options[f"pause_{category}"] and paused_by_category[category]
                        for category in ("battle", "capture", "target")
                    ) or paused_by_category["minigame"] or target_pause or "loot" in self._macro_pause_reasons
                ),
                "resume_remaining": resume_remaining,
                "pause_reasons": pause_reasons,
                "name": self._macro_record_name,
                "actions": len(self._macro_actions),
                "elapsed": min(elapsed, self._macro_record_duration),
                "duration": self._macro_record_duration,
            }

    def list_macros(self):
        if not self.macros_dir.exists():
            return []
        return sorted(path.stem for path in self.macros_dir.glob("*.json"))

    def get_macro_options(self, name):
        safe_name = _safe_name(name)
        path = self.macros_dir / f"{safe_name}.json"
        try:
            with path.open(encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError as e:
            raise ValueError("Macro não encontrada.") from e
        if isinstance(data, dict):
            return self._normalize_macro_options(
                data.get("pause_options"),
                data.get("pause_on_events", True),
                data.get("resume_delay_seconds", 0),
            )
        return self._default_macro_options()

    def set_macro_pause_on_events(self, name, pause_on_events):
        options = self.get_macro_options(name)
        for category in ("battle", "capture", "target"):
            options[f"pause_{category}"] = bool(pause_on_events)
        return self.set_macro_options(name, options)

    def set_macro_options(self, name, options):
        safe_name = _safe_name(name)
        path = self.macros_dir / f"{safe_name}.json"
        options = self._normalize_macro_options(options)
        with self._macro_lock:
            if self._macro_recording or self._macro_playing:
                raise RuntimeError("Pare a gravação ou reprodução antes de alterar as opções da macro.")
            try:
                with path.open(encoding="utf-8") as f:
                    data = json.load(f)
            except FileNotFoundError as e:
                raise ValueError("Macro não encontrada.") from e
            if isinstance(data, list):
                data = {"version": 1, "actions": data}
            if not isinstance(data, dict) or not isinstance(data.get("actions"), list):
                raise ValueError("A macro tem formato inválido.")
            data["pause_options"] = options
            temp = path.with_suffix(".tmp")
            with temp.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            temp.replace(path)
        return options

    def delete_macro(self, name):
        if not str(name or "").strip():
            raise ValueError("Selecione uma macro para excluir.")
        safe_name = _safe_name(name)
        path = self.macros_dir / f"{safe_name}.json"
        with self._macro_lock:
            if self._macro_recording or self._macro_playing:
                raise RuntimeError("Pare a gravação ou reprodução antes de excluir uma macro.")
            try:
                path.unlink()
            except FileNotFoundError as e:
                raise ValueError("A macro selecionada não existe mais.") from e
        self.log(f"Macro '{safe_name}' excluída.", "info")
        return self.list_macros()
    def _focus_game_window(self):
        windows = self._find_game_windows()
        window = next((w for w in windows if not w.isMinimized), windows[0] if windows else None)
        if not window:
            raise RuntimeError(f"Janela '{self.config['window_title']}' não encontrada.")
        try:
            if window.isMinimized:
                window.restore()
            window.activate()
        except Exception as e:
            raise RuntimeError(f"Não consegui trazer o jogo para frente: {e}") from e
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and not self._game_focused():
            time.sleep(0.05)
        if not self._game_focused():
            raise RuntimeError("O jogo não ficou em primeiro plano.")

    def start_macro_recording(self, name, max_seconds=None, pause_on_events=True, resume_delay_seconds=0, event_options=None, arrows_only=False):
        if not str(name or "").strip():
            raise ValueError("Digite um nome para a macro.")
        name = _safe_name(name)
        duration = float(max_seconds if max_seconds is not None else self.config["macro"]["max_seconds"])
        if not 1 <= duration <= 600:
            raise ValueError("A duração deve ficar entre 1 e 600 segundos.")
        record_options = self._normalize_macro_options(event_options, pause_on_events, resume_delay_seconds)
        if self.any_running() and not arrows_only:
            raise RuntimeError("Pare os módulos do bot antes de gravar, para não registrar ações automáticas.")
        if not self._ensure_interception():
            raise RuntimeError("O driver Interception não está disponível.")
        with self._macro_lock:
            if self._macro_recording or self._macro_playing:
                raise RuntimeError("Já existe uma gravação ou reprodução em andamento.")
        self._focus_game_window()
        stop = threading.Event()
        with self._macro_lock:
            self._macro_recording = True
            self._macro_record_stop = stop
            self._macro_record_started = time.monotonic()
            self._macro_record_duration = duration
            self._macro_record_name = name
            self._macro_record_options = record_options
            self._macro_record_arrows_only = bool(arrows_only)
            self._macro_actions = []
        self.config["macro"]["max_seconds"] = duration
        self.config["macro"]["arrows_only"] = bool(arrows_only)
        self.save_config(self.config, quiet=True)
        thread = threading.Thread(target=self._record_macro, args=(stop,), daemon=True, name="macro-record")
        self._macro_record_thread = thread
        thread.start()
        self.log(f"Gravando rota: {name}.", "info", "macro")
        self.log(f"Gravando '{name}' por até {int(duration)} s.", "success")
        return self.macro_state()

    def _record_macro(self, stop):
        arrow_vks = {0x25, 0x26, 0x27, 0x28}
        watched = arrow_vks if self._macro_record_arrows_only else set(MACRO_KEYS)
        watched.discard(VK_CODES.get(str(self.config.get("stop_hotkey", "F12")).upper()))
        previous = set()
        started = time.monotonic()
        actions = []
        try:
            while not stop.is_set() and time.monotonic() - started < self._macro_record_duration:
                current = {vk for vk in watched if _user32.GetAsyncKeyState(vk) & 0x8000}
                if self._game_focused():
                    elapsed = round((time.monotonic() - started) * 1000)
                    for vk in sorted(current - previous):
                        if vk in MACRO_KEYS:
                            actions.append({"t": elapsed, "type": "press", "key": MACRO_KEYS[vk]})
                        if len(actions) >= MAX_MACRO_ACTIONS:
                            self.log("Limite de ações atingido; encerrando a gravação.", "warn")
                            stop.set()
                            break
                previous = current
                stop.wait(0.01)
        except Exception as e:
            self.log(f"Erro durante a gravação: {e}", "error")
        finally:
            with self._macro_lock:
                name = self._macro_record_name
                self._macro_actions = actions
            if actions:
                try:
                    self.macros_dir.mkdir(parents=True, exist_ok=True)
                    path = self.macros_dir / f"{name}.json"
                    temp = path.with_suffix(".tmp")
                    with temp.open("w", encoding="utf-8") as f:
                        json.dump({"version": 1, "pause_options": self._macro_record_options, "actions": actions}, f, ensure_ascii=False, indent=2)
                    temp.replace(path)
                    self.log(f"Rota gravada '{name}' salva com {len(actions)} comandos.", "success", "macro")
                except OSError as e:
                    self.log(f"Não consegui salvar a macro: {e}", "error")
            else:
                self.log("Nenhuma ação foi gravada; a macro anterior foi mantida.", "warn")
            with self._macro_lock:
                self._macro_recording = False
                self._macro_record_stop = None
                self._macro_record_thread = None

    def stop_macro_recording(self):
        with self._macro_lock:
            stop = self._macro_record_stop
            thread = self._macro_record_thread
        if stop:
            stop.set()
        if thread and thread is not threading.current_thread():
            thread.join(timeout=3)
        return self.macro_state()

    def play_macro(self, name, loop=False):
        safe_name = _safe_name(name)
        path = self.macros_dir / f"{safe_name}.json"
        try:
            with path.open(encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError as e:
            raise ValueError("Macro não encontrada.") from e
        actions = data.get("actions") if isinstance(data, dict) else data
        macro_options = self._normalize_macro_options(
            data.get("pause_options") if isinstance(data, dict) else None,
            data.get("pause_on_events", True) if isinstance(data, dict) else True,
            data.get("resume_delay_seconds", 0) if isinstance(data, dict) else 0,
        )
        if self.config["battle"].get("long_battle_enabled"):
            delay = float(self.config["battle"].get("long_battle_after", 0) or 0)
            battle = self.config["battle"]
            key = str(battle.get("long_battle_key") or "").strip().lower()
            repeats = int(battle.get("long_battle_repeats", 1))
            if not 1 <= delay <= 3600:
                raise ValueError("Configure o tempo preso em combate entre 1 e 3600 segundos.")
            if key not in MACRO_KEYS.values() or not 1 <= repeats <= 20:
                raise ValueError("Configure uma tecla extra válida e de 1 a 20 repetições na aba Batalha.")
        if not isinstance(actions, list) or not actions or len(actions) > MAX_MACRO_ACTIONS:
            raise ValueError("A macro está vazia ou tem formato inválido.")
        last_time = -1
        for action in actions:
            if not isinstance(action, dict) or action.get("type") not in ("press", "click"):
                raise ValueError("A macro contém uma ação inválida.")
            at = action.get("t")
            if not isinstance(at, (int, float)) or at < last_time or at < 0:
                raise ValueError("Os tempos da macro estão inválidos.")
            last_time = at
            if action["type"] == "press" and action.get("key") not in MACRO_KEYS.values():
                raise ValueError("A macro contém uma tecla não suportada.")
            if action["type"] == "click" and any(
                not isinstance(action.get(k), int) or not 0 <= action[k] <= 32767 for k in ("x", "y")
            ):
                raise ValueError("A macro contém coordenadas inválidas.")
        if not self._ensure_interception():
            raise RuntimeError("O driver Interception não está disponível.")
        with self._macro_lock:
            if self._macro_recording or self._macro_playing:
                raise RuntimeError("Já existe uma gravação ou reprodução em andamento.")
        self._focus_game_window()
        stop = threading.Event()
        with self._macro_lock:
            self._macro_playing = True
            self._macro_play_options = macro_options
            self._macro_battle_started_at = None
            self._macro_battle_skip_logged = False
            self._macro_play_loop = bool(loop)
            self._macro_play_stop = stop
        thread = threading.Thread(target=self._play_macro, args=(stop, safe_name, actions, macro_options, bool(loop)), daemon=True, name="macro-play")
        self._macro_play_thread = thread
        thread.start()
        return self.macro_state()

    def _play_macro(self, stop, name, actions, macro_options=None, loop=False):
        macro_options = self._normalize_macro_options(macro_options)
        last_action = None
        self.log(f"Reproduzindo a rota gravada '{name}'.", "info", "macro")
        try:
            while not stop.is_set():
                last_time = 0
                for action in actions:
                    command_interval = float(macro_options.get("command_interval", 0) or 0)
                    if command_interval > 0:
                        action_delay = command_interval if last_action is not None else 0
                    else:
                        action_delay = (action["t"] - last_time) / 1000
                    if not self._wait_macro_delay(
                        stop,
                        action_delay,
                        macro_options,
                        interrupted_action=last_action,
                    ):
                        break
                    if stop.is_set():
                        break
                    self._emit_macro_action(action)
                    last_action = action
                    last_time = action["t"]
                if not loop or stop.is_set():
                    break
                if not float(macro_options.get("command_interval", 0) or 0):
                    stop.wait(0.1)
            self.log(f"Macro '{name}' {'interrompida' if stop.is_set() else 'reproduzida'}.", "info", "macro")
        except Exception as e:
            self.log(f"Erro ao reproduzir macro '{name}': {e}", "error")
        finally:
            with self._macro_lock:
                self._macro_playing = False
                self._macro_play_loop = False
                self._macro_play_stop = None
                self._macro_play_thread = None
            self._macro_battle_started_at = None
            self._macro_battle_skip_logged = False

    def _macro_is_paused(self, macro_options=None):
        macro_options = self._normalize_macro_options(macro_options)
        battle_reason = "battle:macro_detector"
        capture_reason = "capture:macro_detector"
        target_reason = "battle_target:detector"
        long_battle_action_enabled = bool(self.config["battle"].get("long_battle_enabled"))
        skip_battle_after = float(macro_options.get("skip_battle_after", 0) or 0)

        battle_has_reference = self._template("battle/batalha_vazia.png") is not None
        battle_active = (
            not self._battle_empty(trigger_long_action=long_battle_action_enabled)
            if battle_has_reference and (
                macro_options["pause_battle"] or long_battle_action_enabled or skip_battle_after > 0
            )
            else False
        )
        now = time.monotonic()
        if battle_active:
            if self._macro_battle_started_at is None:
                self._macro_battle_started_at = now
                self._macro_battle_skip_logged = False
            battle_timed_out = bool(
                skip_battle_after > 0 and now - self._macro_battle_started_at >= skip_battle_after
            )
        else:
            self._macro_battle_started_at = None
            self._macro_battle_skip_logged = False
            battle_timed_out = False
        battle_pause_active = battle_active and not battle_timed_out
        if battle_timed_out and not self._macro_battle_skip_logged:
            self.log(
                f"Combate excedeu {skip_battle_after:g}s; liberando a rota e mantendo a pausa de captura se necessaria.",
                "warn",
                "macro",
            )
            self._macro_battle_skip_logged = True
        capture_visible = (
            self._macro_capture_target_present()
            if macro_options["pause_capture"]
            else False
        )
        target_present = (
            self._battle_target_is_present()
            if macro_options["pause_target"]
            else False
        )

        # A reprodução consulta os sinais diretamente; ela não depende de os
        # módulos Batalha/Captura estarem rodando para manter suas pausas.
        self._set_macro_pause(battle_reason, macro_options["pause_battle"] and battle_pause_active)
        self._set_macro_pause(capture_reason, macro_options["pause_capture"] and capture_visible)
        if target_present:
            self._set_macro_pause(target_reason, macro_options["pause_target"] and not battle_timed_out)
        elif macro_options["pause_target"]:
            with self._macro_pause_lock:
                target_was_paused = target_reason in self._macro_pause_reasons_by_category["target"]
            should_hold = False
            if target_was_paused:
                should_hold = battle_pause_active
                with self._battle_targets_lock:
                    last_seen = self._battle_target_last_seen_at
                capture_interval = float(self.config["capture"].get("interval", 0.5) or 0.5)
                capture_grace = max(1.5, min(capture_interval, 10.0))
                should_hold = should_hold or time.monotonic() - last_seen < capture_grace
            self._set_macro_pause(target_reason, should_hold)
        else:
            self._set_macro_pause(target_reason, False)

        focused = not self.config["safety"].get("pause_unfocused", True) or self._game_focused()
        if not focused:
            return True
        now = time.monotonic()
        with self._macro_pause_lock:
            if "loot" in self._macro_pause_reasons:
                return True
            if self._macro_pause_reasons_by_category["minigame"]:
                return True
            for category in ("battle", "capture", "target"):
                if macro_options[f"pause_{category}"] and (
                    self._macro_pause_reasons_by_category[category] or now < self._macro_resume_after[category]
                ):
                    return True
        return False

    def _macro_capture_target_present(self):
        now = time.monotonic()
        with self._macro_capture_detector_lock:
            if now - self._macro_capture_detector_checked_at < 0.35:
                return self._macro_capture_detector_cache
            self._macro_capture_detector_checked_at = now
        try:
            detected = self._capture_target_visible()
        except Exception as error:
            self.log(f"Falha ao verificar alvos de captura para pausar a rota: {error}", "warn", "macro")
            detected = False
        with self._macro_capture_detector_lock:
            if detected:
                self._macro_capture_detector_misses = 0
                self._macro_capture_last_seen_at = now
                self._macro_capture_detector_cache = True
            else:
                self._macro_capture_detector_misses += 1
                clear_after = max(0.6, 2 * 0.35)
                if (
                    self._macro_capture_detector_misses >= 2
                    and now - self._macro_capture_last_seen_at >= clear_after
                ):
                    self._macro_capture_detector_cache = False
            return self._macro_capture_detector_cache

    def _macro_event_pause_active(self, macro_options):
        now = time.monotonic()
        with self._macro_pause_lock:
            return any(
                macro_options[f"pause_{category}"]
                and (
                    self._macro_pause_reasons_by_category[category]
                    or now < self._macro_resume_after[category]
                )
                for category in ("battle", "capture", "target")
            ) or bool(self._macro_pause_reasons_by_category["minigame"]) or "loot" in self._macro_pause_reasons

    def _emit_macro_action(self, action):
        """Emite um único comando gravado, sem combinar eventos adjacentes."""
        with self._macro_action_lock:
            if action["type"] == "press":
                ic.press(action["key"])
            else:
                x, y = self._abs_point((action["x"], action["y"]))
                self._click(x, y)

    def _wait_macro_delay(self, stop, seconds, macro_options=None, interrupted_action=None):
        """Congela o tempo durante pausas; ao voltar de encontro, repete o último comando."""
        remaining = max(0.0, float(seconds))
        previous = time.monotonic()
        while not stop.is_set():
            now = time.monotonic()
            if self._macro_is_paused(macro_options):
                interrupted_by_event = self._macro_event_pause_active(macro_options)
                previous = now
                while not stop.is_set() and self._macro_is_paused(macro_options):
                    interrupted_by_event = interrupted_by_event or self._macro_event_pause_active(macro_options)
                    previous = time.monotonic()
                    stop.wait(0.05)
                if stop.is_set():
                    break
                if interrupted_by_event and interrupted_action is not None:
                    self._emit_macro_action(interrupted_action)
                    command = interrupted_action.get("key", "clique")
                    self.log(f"Encontro resolvido; repetindo o comando interrompido ({command}).", "info", "macro")
                previous = time.monotonic()
                continue
            if remaining <= 0:
                break
            remaining -= now - previous
            previous = now
            if remaining > 0:
                stop.wait(min(0.05, remaining))
        return not stop.is_set()

    def stop_macro_playback(self):
        with self._macro_lock:
            stop = self._macro_play_stop
        if stop:
            stop.set()
        return self.macro_state()

    # ---------- troca de Pokémon ----------
    def add_switch_slot(self, name):
        name = str(name or "").strip()[:40]
        if not name:
            raise ValueError("Digite o nome do Pokémon ou do atalho.")
        slots = self.config["switch"]["slots"]
        slot = {"id": f"{time.time_ns():x}-{len(slots):x}", "name": name, "point": None}
        slots.append(slot)
        self.save_config(self.config, quiet=True)
        return copy.deepcopy(slot)

    def set_switch_slot_point(self, slot_id, x, y):
        point = [int(x), int(y)]
        if any(value < 0 or value > 32767 for value in point):
            raise ValueError("Coordenadas inválidas.")
        slot = next((s for s in self.config["switch"]["slots"] if s["id"] == slot_id), None)
        if not slot:
            raise ValueError("Posição de troca não encontrada.")
        slot["point"] = point
        self.save_config(self.config, quiet=True)
        return copy.deepcopy(slot)

    def move_switch_slot(self, slot_id, delta):
        slots = self.config["switch"]["slots"]
        index = next((i for i, slot in enumerate(slots) if slot["id"] == slot_id), None)
        if index is None:
            raise ValueError("Posição de troca não encontrada.")
        target = max(0, min(len(slots) - 1, index + int(delta)))
        if target != index:
            slots[index], slots[target] = slots[target], slots[index]
            self.save_config(self.config, quiet=True)
        return copy.deepcopy(slots)

    def remove_switch_slot(self, slot_id):
        slots = self.config["switch"]["slots"]
        self.config["switch"]["slots"] = [s for s in slots if s["id"] != slot_id]
        self.save_config(self.config, quiet=True)

    def click_switch_slot(self, slot_id):
        slot = next((s for s in self.config["switch"]["slots"] if s["id"] == slot_id), None)
        if not slot or not slot.get("point"):
            raise ValueError("Marque primeiro a posição desse Pokémon no jogo.")
        if self.is_running("switch"):
            raise RuntimeError("Desative a troca automática antes de trocar manualmente.")
        if self.macro_state()["recording"] or self.macro_state()["playing"]:
            raise RuntimeError("Pare a gravação ou reprodução antes de trocar.")
        if not self._ensure_interception():
            raise RuntimeError("O driver Interception não está disponível.")
        self._focus_game_window()
        x, y = self._abs_point(slot["point"])
        self._click(x, y)
        self.log(f"Clique de troca enviado: {slot['name']}.", "success")
        return True


