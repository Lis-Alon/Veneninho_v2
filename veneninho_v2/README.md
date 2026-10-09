# Veneno do Pokémon

Bot para otPokemon (Windows) com painel gráfico: liga e desliga batalha, captura, cavebot e cura sem usar o terminal.

- **Painel**: veja [`game-bot-dashboard/`](game-bot-dashboard/README.md). Para gerar o `.exe`, dê dois cliques em `game-bot-dashboard/build.bat`.
- **Scripts originais** (rodam no terminal): `battle.py`, `captura.py`, `cave bot.py`, `opacity.py`
- **Imagens** usadas na detecção: `imags/` (`battle/`, `captura/`, `map/`)

Projeto original: [Lis-Alon/Veneno_do_pokemon](https://github.com/Lis-Alon/Veneno_do_pokemon).

## Como instalar e rodar (passo a passo)

O bot só funciona no **Windows**, porque usa a API de janelas do Windows e o driver Interception para mandar teclado e mouse.

### 1. Instale o Python

1. Baixe o Python 3.10 ou mais novo em [python.org/downloads](https://www.python.org/downloads/).
2. Na primeira tela do instalador, marque **"Add python.exe to PATH"** e clique em **Install Now**.
3. Abra o **Prompt de Comando** (`cmd`) e confira:

   ```
   python --version
   ```

### 2. Instale o driver Interception

O bot usa esse driver para apertar teclas e clicar dentro do jogo.

1. Baixe o `Interception.zip` na página de [releases do Interception](https://github.com/oblitum/Interception/releases) e extraia.
2. Abra o **Prompt de Comando como administrador** (botão direito no `cmd` > *Executar como administrador*).
3. Entre na pasta `command line installer` que veio no zip e rode:

   ```
   install-interception.exe /install
   ```

4. **Reinicie o computador.** O driver só passa a funcionar depois de reiniciar.

### 3. Baixe o projeto

Com o [Git](https://git-scm.com/download/win) instalado:

```
git clone https://github.com/CarlosGabriel-UX/veneno_pokemon.git
cd veneno_pokemon
```

Sem Git: no GitHub, clique em **Code > Download ZIP**, extraia e abra o `cmd` dentro da pasta extraída.

### 4. Rode o painel (recomendado)

O painel tem todos os módulos (batalha, captura, cavebot e cura) com botões para ligar e desligar.

```
cd game-bot-dashboard
pip install -r requirements.txt
python src/main.py
```

Depois:

1. Abra o jogo e entre no personagem. O painel procura a janela pelo título `otPokemon | Lisalon | South America`; se o seu for diferente, troque em **Ajustes > Título da janela**.
2. Em **Ajustes**, marque as coordenadas: deixe o mouse no ponto do jogo e aperte **F8**.
3. Ligue os módulos que quiser. Para parar tudo, use o botão no topo ou aperte **F12**.

Os indicadores **Jogo** e **Driver** no topo do painel mostram se a janela foi encontrada e se o Interception carregou. Se o **Driver** ficar vermelho, volte ao passo 2.

### 5. Ou gere o .exe (para quem não quer usar o terminal)

Dê dois cliques em `game-bot-dashboard\build.bat`. Ele instala o que falta, gera `game-bot-dashboard\dist\VenenoBot\VenenoBot.exe` e copia a pasta `imags` para junto. Para levar para outro PC, compacte a pasta `dist\VenenoBot` inteira (o outro PC também precisa do driver do passo 2).

### Scripts originais (opcional)

Os scripts da raiz fazem uma coisa cada e não têm painel. Instale as dependências e rode **sempre a partir da raiz do projeto**, porque eles procuram as imagens em `imags/`:

```
pip install pyautogui opencv-python pillow interception-python pywin32 pygetwindow
python battle.py
python captura.py
python "cave bot.py"
python opacity.py 200
```

- As coordenadas da tela (`tela`, `regiao_mapa`) estão fixas no código para um monitor 1920x1080; ajuste no começo de cada script se a sua tela for diferente.
- Para parar um script, feche o terminal ou aperte `Ctrl+C`.
- O `requirements.txt` da raiz também instala `torch`, `torchvision` e `scipy`, que só o `teste captcha.py` usa. São pacotes grandes; só instale com `pip install -r requirements.txt` se for usar esse teste.

### Problemas comuns

| Sintoma | O que fazer |
| --- | --- |
| `'python' não é reconhecido como comando` | Reinstale o Python marcando "Add python.exe to PATH". |
| `interception não carregou` ou indicador **Driver** vermelho | Instale o driver (passo 2) como administrador e reinicie o PC. |
| `Janela '...' não encontrada` | Abra o jogo e confira o título da janela em **Ajustes**. |
| O bot não acha nada na tela | Deixe o jogo visível e em primeiro plano; os módulos pausam quando o jogo está atrás de outra janela. |
| Script dá erro de arquivo de imagem | Rode o script de dentro da pasta raiz do projeto. |
