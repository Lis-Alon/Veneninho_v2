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
    },
    "capture": {
        "key": "1",
        "interval": 0.5,
        "confidence": 0.75,
        "region": [3, 28, 1910, 993],
        "targets": ["croa.png", "croa_2.png"],
        "alert_on": [],  # avisa com som quando aparecer, mesmo sem capturar
    },
    "cavebot": {
        "walk_time": 9,
        "confidence": 0.8,
        "map_region": [1730, 57, 182, 270],
        "hp_pixel": [1748, 286],
        "hp_color": [255, 0, 0],
        "route": [],  # [{"name": "1.png", "time": 9}, ...]; vazio = todos de imags/map em ordem
    },
    "heal": {
        "key": "f1",
        "pixel": [],  # ponto da barra de vida do SEU pokémon; vazio = não configurado
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
    },
    "switch": {
        "slots": [],
        "interval": 60,
    },
    "pairing": {
        "region": [],  # recorte que contém as duas fileiras de três ícones
    },
    "hotkeys": {"battle": "", "capture": "", "cavebot": "", "heal": "", "switch": "", "pairing": ""},
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
    "pairing": "Pareamento",
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
        self._status = {name: "Parado" for name in MODULES}
        self._ic_ready = False
        self._ic_lock = threading.Lock()
        self._templates = {}
        self._pairing_model = None
        self._pairing_preprocess = None
        self._pairing_device = None
        self._pairing_pause = threading.Event()
        self._pause_state_lock = threading.Lock()
        self._paused_modules = set()
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
        self._macro_play_stop = None
        self._macro_play_thread = None

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
            if module != "pairing" and self._pairing_pause.is_set():
                if not acknowledged_pause:
                    with self._pause_state_lock:
                        self._paused_modules.add(module)
                        self._status[module] = "Pausado: minigame em execução"
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

    def map_waypoints(self):
        files = [p for p in (self.img / "map").glob("*.png") if p.stem.isdigit()]
        return [p.name for p in sorted(files, key=lambda p: int(p.stem))]

    def route(self):
        c = self.config["cavebot"]
        if c["route"]:
            return [(wp["name"], float(wp.get("time", c["walk_time"]))) for wp in c["route"]]
        return [(name, float(c["walk_time"])) for name in self.map_waypoints()]

    def add_capture_image(self, name, src_path=None, region=None):
        """Adiciona um pokémon à pasta de captura, de um arquivo ou recortando a tela."""
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
        self.save_config(self.config, quiet=True)
        self.log(f"'{path.stem}' movido para imags/captura/removidos.", "info")

    def remove_waypoint(self, name):
        self._move_to_trash("map", name)
        c = self.config["cavebot"]
        c["route"] = [r for r in c["route"] if r["name"] != name]
        self.save_config(self.config, quiet=True)
        self.log(f"Ponto {Path(name).stem} movido para imags/map/removidos.", "info")

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
            region = None
            conf = cfg["battle"]["confidence"]
            names = ["battle/batalha_vazia.png"]
            max_w = 1000
        elif kind == "pairing":
            region = self._abs_region(cfg["pairing"]["region"])
            if not region or len(region) != 4 or min(region[2:]) < 1:
                raise ValueError("Marque e salve a área completa do minigame em Ajustes > Pareamento.")
            img, offset = self._grab(region)
            present, presence_score = self._minigame_present(img)
            if not present:
                self.log("Teste do minigame: tela do minigame não detectada; nenhuma ação foi executada.",
                         "info", "pairing")
                return {"kind": kind, "active": False, "presence_score": round(presence_score, 3),
                        "confidence": 0, "image": _jpeg_data_uri(img, 1000), "results": [],
                        "pairs": [], "offset": list(self._offset())}
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
                    "offset": list(self._offset())}
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
        if kind == "battle":
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
        if pick["target"] in ("new_pokemon", "new_waypoint"):
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
        if name == "heal" and len(self.config["heal"]["pixel"]) != 2:
            self.log("Configure o pixel da sua vida em Ajustes > Cura antes de ligar.", "error", name)
            return False
        if name == "pairing":
            region = self.config["pairing"].get("region")
            if not region or len(region) != 4 or min(region[2:]) < 1:
                self.log("Marque e salve a área do minigame em Ajustes > Pareamento antes de ligar.", "error", name)
                return False
        if name == "switch":
            slots = [s for s in self.config["switch"]["slots"] if s.get("point") and len(s["point"]) == 2]
            if len(slots) < 2:
                self.log("Configure pelo menos duas posições em Ações > Troca de Pokémon.", "error", name)
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
                if name == "pairing":
                    self._resume_after_pairing()
                self.stats.module_off(name)
                self.log("Desligado.", "info", name)
                self._status[name] = "Parado"

        self._stops[name] = stop
        t = threading.Thread(target=runner, daemon=True, name=name)
        self._threads[name] = t
        t.start()
        return True

    def stop(self, name):
        ev = self._stops.get(name)
        if ev:
            ev.set()

    def stop_all(self):
        for name in MODULES:
            self.stop(name)
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
    def _battle_empty(self):
        return bool(self._locate("battle/batalha_vazia.png", self.config["battle"]["confidence"]))

    def _click(self, x, y):
        """Move o mouse e clica pelo driver. Não usa ic.click(x, y), que depende do
        _utils interno do interception e quebra quando há outro pacote por cima."""
        pg.moveTo(x, y)
        time.sleep(0.15)
        ic.click()

    def _ball_key(self):
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
                ic.press(self._ball_key())
                time.sleep(0.1)
                self._click(pos.x, pos.y)
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
        """Detecta o cabeçalho e os botões fixos do minigame, ignorando os ícones variáveis."""
        reference = _read_image(self.img / "map" / "areaminigame.png")
        if reference is None or img is None or img.size == 0:
            return False, 0.0
        rh, rw = reference.shape[:2]
        # O título e a barra inferior são estáveis, ao contrário das seis figuras.
        title = cv2.cvtColor(reference[0:max(12, int(rh * 0.13)), int(rw * 0.025):int(rw * 0.975)],
                             cv2.COLOR_BGR2GRAY)
        footer = cv2.cvtColor(reference[int(rh * 0.84):rh, int(rw * 0.025):int(rw * 0.975)],
                              cv2.COLOR_BGR2GRAY)
        screen = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        best = 0.0
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
            _, title_score, _, title_loc = cv2.minMaxLoc(title_map)
            _, footer_score, _, footer_loc = cv2.minMaxLoc(footer_map)
            expected_gap = int((rh * 0.84) * scale)
            actual_gap = footer_loc[1] - title_loc[1]
            same_panel = (abs(actual_gap - expected_gap) <= max(7, int(expected_gap * 0.12))
                          and abs((footer_loc[0] + fw / 2) - (title_loc[0] + tw / 2)) <= max(18, tw * 0.12))
            score = (float(title_score) + float(footer_score)) / 2
            best = max(best, score if same_panel else 0.0)
        return best >= 0.58, best

    def _pause_for_pairing(self, pairing_stop):
        """Wait for other modules to reach a safe point before puzzle clicks."""
        self._pairing_pause.set()
        deadline = time.monotonic() + 60
        while not pairing_stop.is_set():
            active = {name for name in MODULES if name != "pairing" and self.is_running(name)}
            with self._pause_state_lock:
                acknowledged = self._paused_modules.copy()
            if active.issubset(acknowledged):
                if active:
                    self.log("Outros módulos pausados para resolver o minigame.", "info", "pairing")
                return True
            if time.monotonic() >= deadline:
                self.log("Não consegui pausar todos os módulos a tempo; não vou clicar no minigame.",
                         "error", "pairing")
                self._pairing_pause.clear()
                return False
            pairing_stop.wait(0.1)
        self._pairing_pause.clear()
        return False

    def _resume_after_pairing(self):
        self._pairing_pause.clear()

    def _run_pairing(self, stop):
        self.log("Aguardando o minigame aparecer na região marcada...", "info", "pairing")
        region = self._abs_region(self.config["pairing"]["region"])
        while self._wait_ready(stop, "pairing"):
            img, offset = self._grab(region)
            present, _ = self._minigame_present(img)
            if not present:
                stop.wait(0.5)
                continue

            self.log("Minigame detectado; calculando os pares...", "info", "pairing")
            if not self._pause_for_pairing(stop):
                if stop.is_set():
                    return
                stop.wait(0.5)
                continue
            pairs, similarities, centers = self._resolve_pairs(img)
            for top_idx, bottom_idx in pairs:
                if stop.is_set() or not self._wait_ready(stop, "pairing"):
                    return
                top = centers[top_idx]
                bottom = centers[3 + bottom_idx]
                self._click(offset[0] + top[0], offset[1] + top[1])
                time.sleep(0.12)
                self._click(offset[0] + bottom[0], offset[1] + bottom[1])
                self.log(f"Pareando ícone superior {top_idx + 1} com inferior {bottom_idx + 1} "
                         f"(similaridade {similarities[top_idx, bottom_idx]:.2f}).", "success", "pairing")
                if stop.wait(0.25):
                    return

            # O botão Confirmar fica no canto inferior direito do painel do minigame.
            if not self._wait_ready(stop, "pairing"):
                return
            confirm_x = offset[0] + int(img.shape[1] * 0.75)
            confirm_y = offset[1] + int(img.shape[0] * 0.91)
            self._click(confirm_x, confirm_y)
            self.log("Pares selecionados; cliquei em Confirmar. Verificando o fechamento do minigame...",
                     "info", "pairing")

            # Só considera concluído quando a tela identificada realmente desaparece.
            deadline = time.monotonic() + 5.0
            while not stop.is_set() and time.monotonic() < deadline:
                if not self._wait_ready(stop, "pairing"):
                    return
                check, _ = self._grab(region)
                still_present, _ = self._minigame_present(check)
                if not still_present:
                    self.log("Minigame fechado após confirmar; resolução concluída.", "success", "pairing")
                    break
                stop.wait(0.35)
            else:
                if not stop.is_set():
                    self.log("Cliquei em Confirmar, mas a tela do minigame continua aberta. Parei para evitar repetir os cliques.",
                             "warn", "pairing")
                    return

            # Aguarda a próxima ativação do minigame; não reprocessa a tela já resolvida.
            self._resume_after_pairing()
            stop.wait(0.7)
    def _run_battle(self, stop):
        last = None
        while self._wait_ready(stop, "battle"):
            empty = self._battle_empty()
            if not empty:
                self._attack()
            if empty != last:
                self.log("Sem inimigos na batalha." if empty else "Inimigo detectado, atacando.", "info", "battle")
                # o cavebot já conta as próprias batalhas
                if empty and last is False and not self.is_running("cavebot"):
                    self.stats.kill("battle")
                last = empty
            stop.wait(self.config["battle"]["interval"])

    def _run_capture(self, stop):
        self.log("Procurando pokémons para capturar...", "info", "capture")
        while self._wait_ready(stop, "capture"):
            self._try_capture_once("capture")
            stop.wait(self.config["capture"]["interval"])

    def _run_heal(self, stop):
        last = 0.0
        self.log("Vigiando a vida.", "info", "heal")
        while self._wait_ready(stop, "heal"):
            c = self.config["heal"]
            x, y = self._abs_point(c["pixel"])
            if list(self._pixel(x, y)) != list(c["color"]) and time.time() - last >= c["cooldown"]:
                ic.press(c["key"])
                last = time.time()
                self.stats.heal()
                self.log(f"Vida baixa, apertei {c['key'].upper()}.", "success", "heal")
            stop.wait(c["interval"])

    def _run_cavebot(self, stop):
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
                pos = self._locate(f"map/{wp}", c["confidence"], self._abs_region(c["map_region"]))
                if not pos:
                    continue
                found_any = True
                self.log(f"Indo para o ponto {Path(wp).stem}.", "info", "cavebot")
                prev = pg.position()
                self._click(pos.x, pos.y)
                if stop.wait(walk_time):
                    return
                pg.moveTo(prev)
                if not self._wait_ready(stop, "cavebot"):
                    return
                self._fight_and_capture(stop)
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

    def _fight_and_capture(self, stop):
        if self._battle_empty():
            return
        c = self.config["cavebot"]
        timeout = float(self.config["safety"]["fight_timeout"] or 0)
        self.log("Inimigo na batalha, atacando até morrer.", "info", "cavebot")
        self._attack()
        x, y = self._abs_point(c["hp_pixel"])
        red = tuple(c["hp_color"])
        started = time.time()
        while not stop.is_set() and self._pixel(x, y) == red:
            if timeout and time.time() - started > timeout:
                self.alert(f"Luta passou de {int(timeout)}s, desisti e segui a rota.", "warn", "cavebot")
                return
            if not self._wait_ready(stop, "cavebot"):
                return
            self._attack()
            stop.wait(1)
        if stop.is_set():
            return
        self.stats.kill("cavebot")
        self.log("Monstro derrotado, tentando captura.", "info", "cavebot")
        for _ in range(5):
            if stop.is_set() or not self._try_capture_once("cavebot"):
                break
            stop.wait(self.config["capture"]["interval"])

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
    @property
    def macros_dir(self):
        return self.root / "macros"

    def macro_state(self):
        with self._macro_lock:
            elapsed = time.monotonic() - self._macro_record_started if self._macro_recording else 0.0
            return {
                "recording": self._macro_recording,
                "playing": self._macro_playing,
                "name": self._macro_record_name,
                "actions": len(self._macro_actions),
                "elapsed": min(elapsed, self._macro_record_duration),
                "duration": self._macro_record_duration,
            }

    def list_macros(self):
        if not self.macros_dir.exists():
            return []
        return sorted(path.stem for path in self.macros_dir.glob("*.json"))

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

    def start_macro_recording(self, name, max_seconds=None):
        if not str(name or "").strip():
            raise ValueError("Digite um nome para a macro.")
        name = _safe_name(name)
        duration = float(max_seconds if max_seconds is not None else self.config["macro"]["max_seconds"])
        if not 1 <= duration <= 600:
            raise ValueError("A duração deve ficar entre 1 e 600 segundos.")
        if self.any_running():
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
            self._macro_actions = []
        self.config["macro"]["max_seconds"] = duration
        self.save_config(self.config, quiet=True)
        thread = threading.Thread(target=self._record_macro, args=(stop,), daemon=True, name="macro-record")
        self._macro_record_thread = thread
        thread.start()
        self.log(f"Gravando '{name}' por até {int(duration)} s.", "success")
        return self.macro_state()

    def _record_macro(self, stop):
        watched = set(MACRO_KEYS) | set(MACRO_MOUSE_BUTTONS)
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
                        elif vk in MACRO_MOUSE_BUTTONS and pg is not None:
                            x, y = pg.position()
                            dx, dy = self._offset()
                            actions.append({"t": elapsed, "type": "click", "x": int(x - dx), "y": int(y - dy)})
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
                        json.dump({"version": 1, "actions": actions}, f, ensure_ascii=False, indent=2)
                    temp.replace(path)
                    self.log(f"Macro '{name}' salva com {len(actions)} ações.", "success")
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

    def play_macro(self, name):
        safe_name = _safe_name(name)
        path = self.macros_dir / f"{safe_name}.json"
        try:
            with path.open(encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError as e:
            raise ValueError("Macro não encontrada.") from e
        actions = data.get("actions") if isinstance(data, dict) else data
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
        if self.any_running():
            raise RuntimeError("Pare os módulos do bot antes de reproduzir a macro.")
        if not self._ensure_interception():
            raise RuntimeError("O driver Interception não está disponível.")
        with self._macro_lock:
            if self._macro_recording or self._macro_playing:
                raise RuntimeError("Já existe uma gravação ou reprodução em andamento.")
        self._focus_game_window()
        stop = threading.Event()
        with self._macro_lock:
            self._macro_playing = True
            self._macro_play_stop = stop
        thread = threading.Thread(target=self._play_macro, args=(stop, safe_name, actions), daemon=True, name="macro-play")
        self._macro_play_thread = thread
        thread.start()
        return self.macro_state()

    def _play_macro(self, stop, name, actions):
        last_time = 0
        try:
            for action in actions:
                if stop.wait(max(0, (action["t"] - last_time) / 1000)):
                    break
                if self.config["safety"].get("pause_unfocused", True):
                    while not stop.is_set() and not self._game_focused():
                        stop.wait(0.1)
                if stop.is_set():
                    break
                if action["type"] == "press":
                    ic.press(action["key"])
                else:
                    x, y = self._abs_point((action["x"], action["y"]))
                    self._click(x, y)
                last_time = action["t"]
            self.log(f"Macro '{name}' {'interrompida' if stop.is_set() else 'reproduzida'}.", "info")
        except Exception as e:
            self.log(f"Erro ao reproduzir macro '{name}': {e}", "error")
        finally:
            with self._macro_lock:
                self._macro_playing = False
                self._macro_play_stop = None
                self._macro_play_thread = None

    def stop_macro_playback(self):
        with self._macro_lock:
            stop = self._macro_play_stop
        if stop:
            stop.set()
        return self.macro_state()

    # ---------- troca de pokémon ----------
    def add_switch_slot(self, name):
        name = str(name or "").strip()[:40]
        if not name:
            raise ValueError("Digite o nome do pokémon ou do atalho.")
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

    def remove_switch_slot(self, slot_id):
        slots = self.config["switch"]["slots"]
        self.config["switch"]["slots"] = [s for s in slots if s["id"] != slot_id]
        self.save_config(self.config, quiet=True)

    def click_switch_slot(self, slot_id):
        slot = next((s for s in self.config["switch"]["slots"] if s["id"] == slot_id), None)
        if not slot or not slot.get("point"):
            raise ValueError("Marque a posição desse pokémon no jogo primeiro.")
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


