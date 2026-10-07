import base64
import ctypes
import json
import sys
import threading
import urllib.request
import webbrowser
from pathlib import Path

import webview

from engine import MODULES, PANEL_TITLE, PICK_HOTKEY, BotEngine

VERSION = "1.1.0"
REPO = "Lis-Alon/Veneno_do_pokemon"

if getattr(sys, "frozen", False):
    # .exe gerado pelo PyInstaller: a pasta imags/ e o config.json ficam ao lado do .exe
    ROOT = Path(sys.executable).parent
else:
    # rodando do código-fonte: raiz do repositório (onde está a pasta imags/)
    ROOT = Path(__file__).resolve().parents[2]

# Caminho relativo: o pywebview resolve a partir da pasta do main.py (ou de dentro do .exe)
UI_URL = "ui/index.html"

FULL_SIZE = (1100, 760)
COMPACT_SIZE = (330, 540)


def _version_tuple(v):
    return tuple(int(p) for p in "".join(c for c in v if c.isdigit() or c == ".").split(".") if p)


def check_update():
    """Procura um Release mais novo no GitHub. None se não tiver ou se der erro (sem internet etc.)."""
    try:
        req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                                     headers={"User-Agent": "VenenoBot", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=6) as r:
            data = json.load(r)
        tag = data.get("tag_name", "")
        if tag and _version_tuple(tag) > _version_tuple(VERSION):
            return {"version": tag.lstrip("vV"), "url": data.get("html_url")}
    except Exception:
        pass
    return None


def already_running():
    """Um painel só: dois bots ao mesmo tempo brigariam pelo mouse e teclado."""
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW(None, False, "VenenoDoPokemonPainel")
    if kernel32.GetLastError() != 183:  # ERROR_ALREADY_EXISTS
        return False
    user32 = ctypes.WinDLL("user32")
    user32.FindWindowW.restype = ctypes.c_void_p
    hwnd = user32.FindWindowW(None, PANEL_TITLE)
    if hwnd:
        user32.ShowWindow(ctypes.c_void_p(hwnd), 9)  # SW_RESTORE
        user32.SetForegroundWindow(ctypes.c_void_p(hwnd))
    return True


class API:
    def __init__(self, engine: BotEngine):
        self._engine = engine
        self._window = None
        self._full_size = FULL_SIZE
        self._update = None
        threading.Thread(target=self._check_update, daemon=True).start()

    def _check_update(self):
        self._update = check_update()
        if self._update:
            self._engine.log(f"Versão {self._update['version']} disponível no GitHub.", "success")

    # estado e módulos
    def get_state(self, since=0):
        state = self._engine.state(since)
        state["macro"] = self._engine.macro_state()
        return state

    def toggle_module(self, name, state):
        if state:
            return self._engine.start(name)
        self._engine.stop(name)
        return False

    def stop_all(self):
        self._engine.stop_all()
        self._engine.stop_macro_recording()
        self._engine.stop_macro_playback()

    # configurações e perfis
    def get_config(self):
        return self._engine.config

    def save_config(self, config):
        return self._engine.save_config(config)

    def list_profiles(self):
        return self._engine.list_profiles()

    def save_profile(self, name, config):
        return self._engine.save_profile(name, config)

    def load_profile(self, name):
        return self._engine.load_profile(name)

    def delete_profile(self, name):
        return self._engine.delete_profile(name)

    def set_window_ref(self):
        return self._engine.set_window_ref()

    # imagens
    def get_images(self):
        def encode(folder, name):
            data = (self._engine.img / folder / name).read_bytes()
            return {"name": name, "src": "data:image/png;base64," + base64.b64encode(data).decode()}

        return {
            "modules": MODULES,
            "pick_hotkey": PICK_HOTKEY,
            "capture": [encode("captura", n) for n in self._engine.capture_images()],
            "waypoints": [encode("map", n) for n in self._engine.map_waypoints()],
        }

    def add_pokemon_from_file(self, name):
        kind = getattr(getattr(webview, "FileDialog", None), "OPEN", None) or webview.OPEN_DIALOG
        files = self._window.create_file_dialog(kind, file_types=("Imagens (*.png;*.jpg;*.jpeg;*.bmp)",))
        if not files:
            return None
        path = files[0] if isinstance(files, (list, tuple)) else files
        return self._engine.add_capture_image(name or Path(path).stem, src_path=path)

    def add_pokemon_from_region(self, name, region):
        return self._engine.add_capture_image(name, region=region)

    def remove_pokemon(self, name):
        self._engine.remove_capture_image(name)

    def add_waypoint_from_region(self, region):
        return self._engine.add_waypoint(region)

    def remove_waypoint(self, name):
        self._engine.remove_waypoint(name)

    # calibração e testes
    def start_pick(self, kind, target):
        return self._engine.start_pick(kind, target)

    def cancel_pick(self):
        self._engine.cancel_pick()

    def clear_pick(self):
        self._engine.clear_pick()

    # macros
    def list_macros(self):
        return self._engine.list_macros()

    def delete_macro(self, name):
        return self._engine.delete_macro(name)
    def start_macro_recording(self, name, duration):
        return self._engine.start_macro_recording(name, duration)

    def stop_macro_recording(self):
        return self._engine.stop_macro_recording()

    def play_macro(self, name):
        return self._engine.play_macro(name)

    def stop_macro_playback(self):
        return self._engine.stop_macro_playback()

    # troca de pokémon
    def add_switch_slot(self, name):
        return self._engine.add_switch_slot(name)

    def set_switch_slot_point(self, slot_id, x, y):
        return self._engine.set_switch_slot_point(slot_id, x, y)

    def remove_switch_slot(self, slot_id):
        return self._engine.remove_switch_slot(slot_id)

    def click_switch_slot(self, slot_id):
        return self._engine.click_switch_slot(slot_id)

    def test_detection(self, kind):
        return self._engine.test_detection(kind)

    # timer
    def set_timer(self, minutes=None, at=None):
        return self._engine.set_timer(minutes, at)

    # estatísticas e logs
    def get_stats(self):
        return self._engine.stats.snapshot()

    def reset_stats(self):
        self._engine.stats.reset_history()
        self._engine.log("Histórico de estatísticas zerado.", "info")

    def open_logs_folder(self):
        self._engine.open_logs_folder()

    # versão
    def get_version(self):
        return {"current": VERSION, "update": self._update}

    def open_update(self):
        url = (self._update or {}).get("url") or ""
        if url.startswith("https://github.com/"):
            webbrowser.open(url)

    # janela
    def set_opacity(self, value):
        return self._engine.set_opacity(value)

    def set_compact(self, compact):
        w = self._window
        if compact:
            self._full_size = (w.width, w.height)
            w.resize(*COMPACT_SIZE)
        else:
            w.resize(*self._full_size)
        w.on_top = bool(compact)
        return compact


if __name__ == "__main__":
    if already_running():
        sys.exit(0)
    engine = BotEngine(ROOT, ROOT / "config.json")
    api = API(engine)
    api._window = webview.create_window(
        PANEL_TITLE,
        UI_URL,
        js_api=api,
        width=FULL_SIZE[0],
        height=FULL_SIZE[1],
        min_size=(COMPACT_SIZE[0], 420),
        background_color="#0f0d14",
    )
    webview.start()
    engine.stop_all()
