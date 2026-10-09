# Painel do Veneno do Pokémon

Janela para ligar e desligar o bot sem usar o terminal.

## O que tem

**Módulos** (mesma lógica dos scripts da raiz)
- **Batalha**: ataca (`e`, `q`) quando aparece inimigo (`battle.py`)
- **Captura**: procura os pokémons marcados e joga a pokébola (`captura.py`)
- **Cavebot**: segue a rota do minimapa, luta e captura (`cave bot.py`)
- **Combate**: escolhe entre somente batalha, batalha com ciclo de troca ou apenas troca
- **Troca**: ciclo independente entre os Pokémon na ordem configurada
- **Cura**: aperta a tecla de cura quando um pixel da barra de vida do seu pokémon muda de cor
- **Pareamento**: aguarda o minigame aparecer, pausa os outros módulos, detecta os ícones por embeddings, clica nos pares, confirma e verifica se o painel fechou; em seguida retoma os demais módulos
- **Parar tudo**: botão no topo ou **F12** (configurável); cada módulo também pode ter um atalho próprio

**Segurança**
- Os módulos **pausam sozinhos quando o jogo não está em primeiro plano** (não clicam em outras janelas)
- Se a janela do jogo fechar, tudo para
- O cavebot desiste de uma luta que passar do **tempo máximo** e **para sozinho** se ficar várias voltas sem achar ponto no minimapa
- **Alertas** com som e o painel piscando na barra de tarefas (erro, travamento, jogo fechado, timer, pokémon com 🔔)
- Só abre **um painel por vez**

**Coordenadas que acompanham a janela**: o painel guarda onde a janela do jogo estava quando as coordenadas foram marcadas e ajusta tudo se ela for movida.

**Topo e lateral**
- Indicadores **Jogo** (verde = em foco, amarelo = aberto atrás de outra janela) e **Driver**
- Troca rápida de **perfil**, **modo compacto** (janelinha por cima do jogo) e **timer** (desliga tudo em X tempo ou num horário)
- Aviso quando sai uma **versão nova** no GitHub (precisa publicar um Release com tag tipo `v1.2.0` e mudar `VERSION` em `src/main.py`)

**Abas**
- **Console**: log ao vivo, filtro por módulo e botão para abrir a pasta `logs/` (um arquivo por dia, bom para mandar quando der problema)
- **Captura**: escolha os pokémons clicando nas fotos, configure uma tecla por Pokémon (vazio usa a tecla padrão), 🔔 para só avisar quando aparecer; **+ Adicionar pokémon** (de um arquivo ou recortando direto da tela); **Testar detecção** mostra um print com o que foi achado e a nota de cada imagem
- **Macros**: grava apenas teclas e pode reproduzir junto dos módulos; pausa durante batalhas, ações de captura e o minigame
- **Minigame**: em **Ajustes > Pareamento**, marque o painel inteiro (incluindo título e botão Confirmar), salve e use **Testar pareamento** com o minigame aberto para conferir a prévia sem cliques; depois ligue **Pareamento** e deixe ativo para resolver as próximas rodadas
- **Rota**: arraste para mudar a ordem, ligue/desligue pontos, tempo de caminhada por ponto, **+ Adicionar ponto** recortando do minimapa; **Testar minimapa**
- **Estatísticas**: números da sessão, gráficos dos últimos 14 dias e pokébolas por pokémon (salvo em `estatisticas.db`)
- **Ajustes**: perfis, coordenadas com **Marcar/Pegar** (posicione o mouse no jogo e aperte **F8**), cura, segurança, atalhos, batalha e título da janela

## Arquivos que o painel cria

Ficam na raiz do repositório (ou ao lado do `.exe`):

| Arquivo | O que é |
| --- | --- |
| `config.json` | configurações atuais |
| `perfis/*.json` | perfis salvos |
| `estatisticas.db` | histórico de batalhas e pokébolas |
| `logs/*.txt` | log de cada dia |
| `imags/captura/removidos/` | pokémons removidos pelo painel (dá pra devolver) |

## Rodar pelo código

```
pip install -r requirements.txt
python src/main.py
```

O driver do [Interception](https://github.com/oblitum/Interception) precisa estar instalado, igual nos scripts originais.

## Gerar o .exe

Dê dois cliques em `build.bat`. Ele cria `dist\VenenoBot\VenenoBot.exe` e copia a pasta `imags` pra junto.
Para mandar pra alguém, compacte a pasta `dist\VenenoBot` inteira.

## Estrutura

```
src/
  main.py     janela (pywebview) e API que o JavaScript chama
  engine.py   módulos do bot, detecção de imagem, perfis, estatísticas
  ui/         index.html, style.css, app.js
```
