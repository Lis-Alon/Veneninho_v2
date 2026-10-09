#import para controlar o pc
import pyautogui as pg
import interception as ic
from threading import Thread


#O FAILSAFE do PyAutoGUI não interrompe os cliques enviados pelo Interception.
pg.FAILSAFE = True

#função para evitar crash do programa caso o ícone não seja encontrado
pg.useImageNotFoundException(False)

tela = (3, 28, 1910, 993)

pokemon = {

"imags/captura/croa.png": 2,
"imags/captura/croa_2.png": 2,
}

def captura():

    while True:

        for caminho_pokemon, segundos in pokemon.items():

            print("Procurando Pokémon para capturar...")

            captura = pg.locateCenterOnScreen(caminho_pokemon,region=tela, confidence=0.75)

            if captura:

                print("Pokemon encontrado, iniciando captura...")

                ic.press("1")

                ic.click(x=captura.x, y=captura.y)

                pg.sleep(0.5) # Aguarda 0.5 segundo antes de verificar novamente


            else:

                print("Nenhum Pokémon encontrado para capturar.")

                pg.sleep(0.5) # Aguarda 0.5 segundo antes de verificar novamente

ic.auto_capture_devices(keyboard=True, mouse=True, verbose=True)


captura ()

