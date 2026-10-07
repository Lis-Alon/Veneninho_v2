import pyautogui

def capture_region():
    region = pyautogui.locateOnScreen("imags/map/areaminigame.png", confidence=0.8)
    print(region)

#capture_region()



def pegar_rgb_mouse():
    while True:
        posicao_atual = pyautogui.position("imags/map/areaminigame.png")
        pixel_atual = pyautogui.pixel(posicao_atual.x, posicao_atual.y)

        pyautogui.sleep(0.5)  # Adiciona um pequeno atraso para evitar sobrecarga de CPU

        print(f"x: {posicao_atual.x}, y: {posicao_atual.y}, RGB: {pixel_atual}")


capture_region()

