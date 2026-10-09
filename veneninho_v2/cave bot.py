#import para controlar o pc
import pyautogui as pg
import interception as ic
from threading import Thread


#O FAILSAFE do PyAutoGUI não interrompe os cliques enviados pelo Interception.
pg.FAILSAFE = True

#função para evitar crash do programa caso o ícone não seja encontrado
pg.useImageNotFoundException(False)

regiao_mapa = (1730, 57, 182, 270) #coordenadas da região do mapa na tela

tela = (3, 28, 1910, 993)

#dicionario para associar icone ao tempo de chegada até ele

icones = {
    "imags/map/1.png": 9,
    "imags/map/2.png": 9,
    "imags/map/3.png": 9,
    "imags/map/4.png": 9,
    "imags/map/5.png": 9,
    "imags/map/6.png": 9,
    "imags/map/7.png": 9,
    "imags/map/8.png": 9,
    "imags/map/9.png": 9,
    "imags/map/10.png": 9,
    "imags/map/11.png": 9,
}

pokemon = {

    "imags/captura/croa.png": 2,
    "imags/captura/croa_2.png": 2,
}
#x: 1748, y: 286 RGB: (255,0,0)  #verifica a cor do pixel para saber se tem inimigos na batalha

def aguardar_monstro_morrer():
    while True:
        rgb_atual = pg.pixel(1748, 286)

        if rgb_atual != (255, 0, 0):
            print("monstro morto, aguardando respawn...")               
            #return  # Sai da função quando o monstro morre
            captura()  # Chama a função de captura após o monstro morrer
            return  # Sai da função após a captura
        
        print("monstro vivo, aguardando morte...")

        ic.press("e")
        ic.press("q")

        pg.sleep(1)  # Aguarda 1 segundo antes de verificar novamente
      
def batalha():
    while True:
        battle_vazia = pg.locateCenterOnScreen("imags/battle/batalha_vazia.png", confidence=0.9)
        print("Procurando inimigos na batalha...")

        if battle_vazia:
            print("Sem inimigos na batalha.")
            pg.sleep(0.5)  # Aguarda 0.5 segundo antes de verificar novamente
            return  # Continua o loop para verificar novamente

        else:
            print("Inimigos detectados na batalha. Iniciando ataque...")
            ic.press("e")
            ic.press("q")

            pg.sleep(0.5)

            aguardar_monstro_morrer()  # Chama a função para aguardar o monstro morrer
            captura()  # Chama a função de captura após o monstro morrer

def movimentação():
#repete a rota indefinidamente
    while True:
        for caminho_icone, segundos in icones.items():
            print("Procurando o ícone:", {caminho_icone})

            posicao = pg.locateCenterOnScreen(caminho_icone, region=regiao_mapa, confidence=0.8) #localize o centro desta imagem na tela

            if posicao:
                print("indo para o icone")

                posicao_atual = pg.position()

                ic.click(x=posicao.x, y=posicao.y)
                pg.sleep(segundos)

                pg.moveTo(posicao_atual)

                batalha()  # Chama a função de batalha após chegar ao ícone

            else:
                print("Ícone não encontrado")

def captura():
    while True:
        for caminho_pokemon, segundos in pokemon.items():
            print("Procurando Pokémon para capturar...")
            captura = pg.locateCenterOnScreen(caminho_pokemon,region=tela, confidence=0.75)
            if captura:
                print("Pokemon encontrado, iniciando captura...")
                ic.press("1")
                ic.click(x=captura.x, y=captura.y)
                pg.sleep(0.5)  # Aguarda 0.5 segundo antes de verificar novamente

        else:
            print("Nenhum Pokémon encontrado para capturar.")
            pg.sleep(0.5)  # Aguarda 0.5 segundo antes de verificar novamente

            return  # Sai da função quando não há Pokémon para capturar
        

ic.auto_capture_devices(keyboard=True, mouse=True, verbose=True)

captura_thread = Thread(target=captura)
captura_thread.start()

#captura()
#batalha()
movimentação()

