# Vid2Cards

Transforma vídeos de uma playlist do YouTube em flashcards do Anki + simulado
diário, usando só modelos locais (nenhuma API paga): **faster-whisper** na GPU
pra transcrever e **Ollama** pra gerar os cards.

Roda uma vez por dia via systemd timer, processa só os vídeos novos, e entrega
os cards direto no Anki via **AnkiConnect** (sincronizando com AnkiWeb, pra
aparecer no celular) — além de sempre deixar um `.apkg` de backup em `saida/`.

## Requisitos

- Linux com GPU NVIDIA (testado em RTX 3050 4GB) — driver proprietário + CUDA
  12 + cuDNN 9. Sem GPU, cai pro Whisper em CPU automaticamente (mais lento).
- `ffmpeg`, `yt-dlp`, `ollama`, Python 3.11+.
- Anki Desktop + addon AnkiConnect, se quiser a entrega automática (opcional —
  sem isso, o pipeline só gera o `.apkg` em `saida/`).

## Instalação

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Baixe os modelos do Ollama que for usar (configurável em `config.toml`):

```bash
ollama pull qwen3:14b        # padrão: denso, mais cards por bloco, mais lento
ollama pull qwen3:30b-a3b    # alternativa MoE: ~3x mais rápido, menos cards
```

## Configuração

Edite `config.toml`:

- `playlists.urls`: lista de playlists do YouTube a monitorar. Playlist
  privada: informe `cookies_file` (exportado do navegador).
- `limites.videos_por_execucao`: quantos vídeos processar por dia (padrão 5 —
  em hardware modesto, cada vídeo pode levar dezenas de minutos só na geração
  de cards; ajuste conforme seu hardware).
- `llm.modelo`: modelo do Ollama usado na geração. `llm.modelo_alternativo` é
  usado só pelo comando `bench`.
- `ankiconnect.habilitado`: se `true`, tenta enviar os cards direto pro Anki
  via AnkiConnect (além do `.apkg`). Se o AnkiConnect não responder, o
  pipeline não falha — só gera o `.apkg` normalmente.

## Comandos

```bash
vid2cards run                # sincroniza playlists + processa + exporta (o que o timer roda)
vid2cards sync                # só lista as playlists e registra vídeos novos no banco
vid2cards add <url>           # processa um vídeo avulso, fora de qualquer playlist
vid2cards status               # tabela com o estado de cada vídeo
vid2cards retry [--id ID]     # volta vídeo(s) em erro pra fila
vid2cards regen --id ID       # regera os cards de um vídeo a partir da transcrição salva
vid2cards export --all        # reexporta todos os cards já gerados (apkg + AnkiConnect)
vid2cards bench --arquivo-teste <txt>   # compara os dois modelos do config numa transcrição de teste
```

## Como importar no Anki manualmente

Se não configurou o AnkiConnect, dê dois cliques no `.apkg` mais recente em
`saida/` (ou arraste pra dentro do Anki Desktop). Os decks entram como
`Vid2Cards::<playlist>::<vídeo>`; reimportar nunca duplica cards (GUID
estável por vídeo+pergunta).

## Pipeline — como funciona por dentro

Execução diária (`vid2cards run`), em fases sequenciais (nunca em paralelo,
porque a GPU de 4GB não comporta Whisper e o LLM ao mesmo tempo):

1. **Sync**: lista as playlists, registra vídeos novos no SQLite
   (`data/vid2cards.db`), identificados pelo `video_id` (chave única).
2. **Transcrição**: baixa só o áudio, transcreve com `faster-whisper` na GPU
   (`vad_filter=True`, com fallback automático sem VAD se o resultado vier
   vazio — música/canto às vezes engana o detector de voz), apaga o áudio,
   salva a transcrição em `data/transcricoes/<id>.json`, libera a GPU.
3. **Geração**: divide a transcrição em blocos de ~20.000 caracteres
   (cortando em fim de frase), manda cada bloco pro Ollama com saída
   estruturada (JSON Schema + validação Pydantic, até 3 tentativas), remove
   cards duplicados entre blocos (rapidfuzz), salva em `data/cards/<id>.json`.
   O timestamp de cada card não é só o início do bloco (que pode cobrir
   15-20 minutos de vídeo) — `texto.localizar_tempo` compara o `conceito` do
   card contra os segmentos do Whisper daquele bloco (fuzzy match) e usa o
   início do segmento mais parecido, então o link da fonte leva direto pro
   momento exato onde aquele conceito foi explicado.
4. **Exportação**: monta o `.apkg` do dia (só com o que foi processado nessa
   execução) + o simulado em Markdown, e injeta os cards via AnkiConnect se
   habilitado.

Cada etapa é um checkpoint: se a execução cair no meio, a próxima retoma de
onde parou (nunca retranscreve um vídeo que já tem transcrição salva, nunca
reprocessa um vídeo já exportado). Um lock de arquivo (`data/vid2cards.lock`)
impede duas execuções simultâneas.

## Timer diário (systemd)

```bash
sudo cp deploy/vid2cards.service deploy/vid2cards.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now vid2cards.timer
systemctl list-timers vid2cards.timer
```

O horário é definido em `deploy/vid2cards.timer` (`OnCalendar`) — por padrão
em UTC, então ajuste pro seu fuso se quiser um horário local específico.
`Persistent=true`: se a máquina estiver desligada no horário, roda assim que
ligar.

## AnkiConnect (entrega automática no celular)

Pra receber os decks atualizados direto no Anki do celular, o Anki Desktop
roda headless no servidor (via Xvfb) com o addon AnkiConnect, sincronizando
com uma conta AnkiWeb — o mesmo app no celular, logado na mesma conta, puxa
as atualizações.

```bash
sudo cp deploy/anki-headless.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now anki-headless.service
```

**Login inicial (única vez):** o AnkiConnect não faz login sozinho — é preciso
abrir a tela do Anki headless uma vez pra autenticar no AnkiWeb. Mais fácil
via VNC temporário:

```bash
sudo apt install -y x11vnc
# descubra o display do Xvfb: ps aux | grep Xvfb
x11vnc -display :<N> -auth <caminho-do-Xauthority-do-processo> \
       -passwd <senha-temporaria> -listen localhost -rfbport 5900 -forever &
```

Depois, túnel SSH (`ssh -L 5900:localhost:5900 usuario@servidor`) e qualquer
cliente VNC (no macOS, `Cmd+K` → `vnc://localhost:5900` no Finder já basta).
Dentro do Anki: botão **Sync** → login AnkiWeb → **Upload** na primeira vez.
Depois disso o `x11vnc` pode ser desligado — a sessão fica salva no perfil, e
toda sincronização seguinte é automática (pelo pipeline, via API).

Se em algum momento aparecer um diálogo pedindo pra escolher entre "Upload"
ou "Download" (indica que o servidor e o AnkiWeb divergiram — por exemplo,
depois de mexer manualmente em um dos dois lados), escolha o lado que você
sabe que está certo.

## Diretórios (fora do git)

```
data/               banco SQLite, transcrições e cards já gerados
saida/               .apkg e simulados diários
logs/                logs com rotação
```

## Troubleshooting

- **`faster-whisper` falha ao decodificar áudio** (`TypeError:
  metadata_errors`): incompatibilidade entre a wheel do pacote `av` e o
  `ffmpeg` do sistema em Python muito novo. O projeto já contorna isso
  decodificando via `ffmpeg` CLI direto (`transcribe.py`), não depende do
  `av` pra isso.
- **Transcrição retorna vazia** (0 segmentos) em vídeos com música/canto: o
  VAD classificou tudo como "sem fala". Já tratado com fallback automático
  sem VAD — se acontecer mesmo assim, rode `vid2cards regen` depois de
  apagar o JSON de transcrição problemático.
- **Cards cloze descartados silenciosamente**: confira se o prompt em
  `generate.py` usa `{{{{c1::...}}}}` (4 chaves) — é uma string passada por
  `.format()`, que exige chave dupla escapada pra virar `{{c1::...}}` de
  verdade no texto final.
- **Anki headless não sobe** (`AttributeError: 'NoneType' object has no
  attribute 'replace'`): passe `-l en` explicitamente no `ExecStart` do
  systemd — é um bug de detecção automática de locale nesse ambiente.
