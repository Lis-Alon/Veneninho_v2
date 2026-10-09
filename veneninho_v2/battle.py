#import para controlar o pc
import pyautogui as pg
import interception as ic
from threading import Thread


#O FAILSAFE do PyAutoGUI não interrompe os cliques enviados pelo Interception.
pg.FAILSAFE = True

#função para evitar crash do programa caso o ícone não seja encontrado
pg.useImageNotFoundException(False)

def batalha():
    while True:
        battle_vazia = pg.locateCenterOnScreen("imags/battle/batalha_vazia.png", confidence=0.9)
        print("Procurando inimigos na batalha...")

        if battle_vazia:
            print("Sem inimigos na batalha.")
            pg.sleep(0.5)  # Aguarda 0.5 segundo antes de verificar novamente

        else:
            print("Inimigos detectados na batalha. Iniciando ataque...")
            ic.press("e")
            ic.press("q")

            pg.sleep(0.5)

ic.auto_capture_devices(keyboard=True, mouse=True, verbose=True)            


batalha()