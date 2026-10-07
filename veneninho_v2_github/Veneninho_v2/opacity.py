import argparse
import ctypes
from ctypes import wintypes

import pygetwindow as gw


# Título completo da janela do jogo no Windows.
WINDOW_TITLE = "otPokemon | Lisalon | South America"

# Constantes da API do Windows
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
LWA_ALPHA = 0x00000002


def is_valid_game_title(title):
    """Compara o título completo, sem diferenciar maiúsculas/minúsculas."""
    return title.strip().casefold() == WINDOW_TITLE.casefold()


def get_window_long_ptr(hwnd, index):
    user32 = ctypes.windll.user32
    function = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    function.argtypes = [wintypes.HWND, ctypes.c_int]
    function.restype = ctypes.c_ssize_t
    return function(hwnd, index)


def set_window_long_ptr(hwnd, index, value):
    user32 = ctypes.windll.user32
    function = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
    function.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    function.restype = ctypes.c_ssize_t
    return function(hwnd, index, value)


def apply_opacity_to_game(opacity):
    # Examina todas as janelas para não depender da busca parcial por título.
    potential_windows = gw.getAllWindows()
    valid_windows = [
        window for window in potential_windows
        if window.title and is_valid_game_title(window.title)
    ]

    if not valid_windows:
        print(f"Erro: A janela '{WINDOW_TITLE}' não foi encontrada.")
        print("Títulos de janelas disponíveis:")
        for window in potential_windows:
            if window.title and window.title.strip():
                print(f" - {window.title}")
        return

    user32 = ctypes.windll.user32
    user32.SetLayeredWindowAttributes.argtypes = [
        wintypes.HWND, wintypes.DWORD, wintypes.BYTE, wintypes.DWORD
    ]
    user32.SetLayeredWindowAttributes.restype = wintypes.BOOL

    for window in valid_windows:
        print(f"Janela detectada: '{window.title}'")
        hwnd = window._hWnd

        try:
            ex_style = get_window_long_ptr(hwnd, GWL_EXSTYLE)
            set_window_long_ptr(hwnd, GWL_EXSTYLE, ex_style | WS_EX_LAYERED)
            if not user32.SetLayeredWindowAttributes(hwnd, 0, opacity, LWA_ALPHA):
                raise ctypes.WinError()

            print(f" -> Opacidade aplicada: {opacity}/255.\n")
        except Exception as error:
            print(f" -> Erro ao modificar a janela: {error}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=f"Ajusta a opacidade da janela '{WINDOW_TITLE}'."
    )
    parser.add_argument(
        "opacity",
        nargs="?",
        type=int,
        default=1,
        help="Nível de opacidade entre 0 e 255 (padrão: 200).",
    )
    args = parser.parse_args()

    if not 0 <= args.opacity <= 255:
        parser.error("o valor da opacidade deve ser um inteiro entre 0 e 255")

    apply_opacity_to_game(args.opacity)
