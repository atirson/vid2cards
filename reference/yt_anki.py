#!/usr/bin/env python3
"""
yt_anki.py — Transforma vídeos do YouTube em flashcards do Anki + um simulado.

Princípios de aprendizagem embutidos:
  1. Prática de recuperação (testes): os cards são PERGUNTAS, não resumos,
     e o script gera um simulado separado (simulado_*.md) para fazer antes do Anki.
  2. Repetição espaçada: o Anki agenda as revisões sozinho (ver dicas no final).
  3. Variação: cada conceito vira 2-3 cards com ângulos diferentes
     (definição, aplicação em cenário, "por quê / compare"), e os cards são
     marcados com tags para você misturar temas (intercalação) no Anki.
  4. Elaboração: perguntas de "por quê" e "como se relaciona com..." forçam
     você a conectar ideias, não só decorar.

Uso:
  pip install youtube-transcript-api genanki anthropic yt-dlp faster-whisper
  export ANTHROPIC_API_KEY="sua-chave"      (Windows: set ANTHROPIC_API_KEY=...)
  python yt_anki.py https://youtu.be/XXXX https://www.youtube.com/watch?v=YYYY --deck "Biologia"

  Vídeos sem legenda: o script baixa o áudio e transcreve com Whisper automaticamente.
  Para usar sempre o Whisper (melhor com termos técnicos): acrescente --whisper
  Mais precisão (mais lento): --modelo-whisper medium

Saída:
  <deck>.apkg        -> dê dois cliques para importar no Anki
  simulado_<deck>.md -> teste prático com gabarito no final
"""

import argparse
import hashlib
import json
import re
import sys

import anthropic
import genanki
from youtube_transcript_api import YouTubeTranscriptApi

MODELO_IA = "claude-sonnet-5-5"
IDIOMAS = ["pt", "pt-BR", "en", "es"]  # ordem de preferência das legendas
MAX_CHARS = 60_000                      # corta transcrições muito longas por bloco


# ---------------------------------------------------------------- transcrição
def extrair_id(url: str) -> str:
    m = re.search(r"(?:v=|youtu\.be/|shorts/|embed/|live/)([\w-]{11})", url)
    if m:
        return m.group(1)
    if re.fullmatch(r"[\w-]{11}", url):
        return url
    raise ValueError(f"Não reconheci o link: {url}")


def baixar_transcricao(video_id: str) -> str:
    try:  # versão 1.x da biblioteca
        partes = YouTubeTranscriptApi().fetch(video_id, languages=IDIOMAS)
        textos = [p.text for p in partes]
    except AttributeError:  # versões antigas
        partes = YouTubeTranscriptApi.get_transcript(video_id, languages=IDIOMAS)
        textos = [p["text"] for p in partes]
    return " ".join(t.replace("\n", " ") for t in textos)


def transcrever_audio(video_id: str, modelo: str = "small") -> str:
    """Plano B: baixa só o áudio (yt-dlp) e transcreve localmente com Whisper.
    Modelos: tiny/base (rápidos), small (equilíbrio), medium/large-v3 (mais precisos)."""
    import os
    import yt_dlp
    from faster_whisper import WhisperModel

    opcoes = {"format": "bestaudio/best", "outtmpl": f"{video_id}.%(ext)s", "quiet": True}
    with yt_dlp.YoutubeDL(opcoes) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
        arquivo = ydl.prepare_filename(info)

    print(f"  🎧 Transcrevendo o áudio com Whisper ({modelo}), pode levar alguns minutos...")
    whisper = WhisperModel(modelo, device="auto", compute_type="int8")
    segmentos, _ = whisper.transcribe(arquivo, vad_filter=True)
    texto = " ".join(s.text.strip() for s in segmentos)
    os.remove(arquivo)
    return texto


def obter_texto(video_id: str, forcar_whisper: bool, modelo: str) -> str:
    if not forcar_whisper:
        try:
            return baixar_transcricao(video_id)
        except Exception as e:
            print(f"  ⚠ Sem legenda disponível ({type(e).__name__}); usando Whisper.")
    return transcrever_audio(video_id, modelo)


def dividir(texto: str, tamanho: int = MAX_CHARS):
    return [texto[i:i + tamanho] for i in range(0, len(texto), tamanho)]


# ------------------------------------------------------------------------ IA
PROMPT = """Você é um especialista em ciência da aprendizagem. A partir da
transcrição abaixo, crie material de estudo em português do Brasil.

Regras para os flashcards:
- A transcrição é automática e pode ter erros (palavras trocadas, termos técnicos
  escritos errado): deduza pelo contexto o termo correto e use-o nos cards.
- Identifique os conceitos realmente importantes (ignore piadas, propaganda, "se inscreva").
- Para CADA conceito, crie de 2 a 3 cards com ângulos diferentes:
  * "definicao": pergunta direta e curta sobre o conceito;
  * "aplicacao": um cenário/exemplo novo onde a pessoa precisa aplicar o conceito;
  * "porque": pergunta de "por quê", "o que aconteceria se" ou "compare X com Y".
- Uma ideia por card. Respostas curtas (idealmente até 2 frases).
- Quando fizer sentido, inclua cards "cloze" (texto com {{{{c1::lacuna}}}}) para fatos, fórmulas ou listas.

Regras para o simulado:
- 5 a 8 questões variadas (múltipla escolha e abertas), mais difíceis que os cards,
  misturando conceitos entre si. Gabarito com explicação breve.

Responda APENAS com JSON válido neste formato:
{{
  "tema": "título curto do assunto",
  "cards": [
    {{"tipo": "definicao|aplicacao|porque", "conceito": "nome curto", "frente": "...", "verso": "..."}},
    {{"tipo": "cloze", "conceito": "nome curto", "texto": "A capital é {{{{c1::Brasília}}}}.", "extra": ""}}
  ],
  "simulado": [
    {{"pergunta": "...", "opcoes": ["A) ...", "B) ..."], "resposta": "...", "explicacao": "..."}}
  ]
}}

Título do vídeo/trecho: {titulo}
TRANSCRIÇÃO:
{texto}
"""


def gerar_material(cliente, texto: str, titulo: str) -> dict:
    msg = cliente.messages.create(
        model=MODELO_IA,
        max_tokens=16000,
        messages=[{"role": "user", "content": PROMPT.format(titulo=titulo, texto=texto)}],
    )
    bruto = "".join(b.text for b in msg.content if b.type == "text")
    bruto = bruto[bruto.find("{"): bruto.rfind("}") + 1]  # tira ```json se vier
    return json.loads(bruto)


# --------------------------------------------------------------------- Anki
def id_estavel(texto: str) -> int:
    return int(hashlib.md5(texto.encode()).hexdigest()[:8], 16)


CSS = """
.card { font-family: -apple-system, Segoe UI, Roboto, sans-serif; font-size: 20px;
  text-align: left; max-width: 640px; margin: auto; line-height: 1.45; }
.tipo { font-size: 12px; text-transform: uppercase; letter-spacing: .06em; opacity: .55; }
.cloze { font-weight: bold; color: #2a7ae2; }
"""

MODELO_BASICO = genanki.Model(
    id_estavel("yt_anki_basico_v1"), "YT Anki - Básico",
    fields=[{"name": "Frente"}, {"name": "Verso"}, {"name": "Tipo"}, {"name": "Fonte"}],
    templates=[{
        "name": "Card",
        "qfmt": '<div class="tipo">{{Tipo}}</div>{{Frente}}',
        "afmt": '{{FrontSide}}<hr id="answer">{{Verso}}<br><br><small>{{Fonte}}</small>',
    }],
    css=CSS,
)

MODELO_CLOZE = genanki.Model(
    id_estavel("yt_anki_cloze_v1"), "YT Anki - Cloze",
    fields=[{"name": "Texto"}, {"name": "Extra"}, {"name": "Fonte"}],
    templates=[{
        "name": "Cloze",
        "qfmt": "{{cloze:Texto}}",
        "afmt": "{{cloze:Texto}}<br>{{Extra}}<br><br><small>{{Fonte}}</small>",
    }],
    css=CSS,
    model_type=genanki.Model.CLOZE,
)


def tag(s: str) -> str:
    return re.sub(r"[^\w-]+", "_", s.strip())[:40] or "geral"


def montar_notas(material: dict, fonte: str):
    notas = []
    tema = tag(material.get("tema", "geral"))
    for c in material.get("cards", []):
        tags = [f"tema::{tema}", f"conceito::{tag(c.get('conceito', ''))}",
                f"tipo::{c.get('tipo', 'basico')}"]
        if c.get("tipo") == "cloze" and "{{c" in c.get("texto", ""):
            notas.append(genanki.Note(model=MODELO_CLOZE,
                                      fields=[c["texto"], c.get("extra", ""), fonte],
                                      tags=tags, guid=genanki.guid_for(c["texto"])))
        elif c.get("frente") and c.get("verso"):
            notas.append(genanki.Note(model=MODELO_BASICO,
                                      fields=[c["frente"], c["verso"], c.get("tipo", ""), fonte],
                                      tags=tags, guid=genanki.guid_for(c["frente"])))
    return notas


# ---------------------------------------------------------------- simulado
def escrever_simulado(questoes, deck_nome: str, caminho: str):
    linhas = [f"# Simulado — {deck_nome}", "",
              "Faça ANTES de abrir o Anki, sem consultar nada. Depois confira o gabarito.",
              "Dica: responda em um lugar diferente do que você assistiu aos vídeos.", ""]
    for i, q in enumerate(questoes, 1):
        linhas.append(f"**{i}.** {q['pergunta']}")
        linhas += [f"   {o}" for o in q.get("opcoes", [])]
        linhas.append("")
    linhas += ["---", "## Gabarito", ""]
    for i, q in enumerate(questoes, 1):
        linhas.append(f"**{i}.** {q['resposta']} — {q.get('explicacao', '')}")
    with open(caminho, "w", encoding="utf-8") as f:
        f.write("\n".join(linhas) + "\n")


# --------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="YouTube -> flashcards Anki + simulado")
    ap.add_argument("videos", nargs="+", help="links ou IDs de vídeos do YouTube")
    ap.add_argument("--deck", default="Estudos YouTube", help="nome do baralho")
    ap.add_argument("--whisper", action="store_true",
                    help="ignora legendas e sempre transcreve o áudio (mais preciso)")
    ap.add_argument("--modelo-whisper", default="small",
                    help="tiny, base, small, medium ou large-v3 (padrão: small)")
    args = ap.parse_args()

    cliente = anthropic.Anthropic()  # lê ANTHROPIC_API_KEY do ambiente
    deck = genanki.Deck(id_estavel(args.deck), args.deck)
    simulado = []

    for url in args.videos:
        try:
            vid = extrair_id(url)
            print(f"▶ Baixando transcrição de {vid}...")
            texto = obter_texto(vid, args.whisper, args.modelo_whisper)
        except Exception as e:
            print(f"  ✗ Pulei {url}: {e}", file=sys.stderr)
            continue

        fonte = f'<a href="https://youtu.be/{vid}">youtu.be/{vid}</a>'
        blocos = dividir(texto)
        for n, bloco in enumerate(blocos, 1):
            print(f"  🤖 Gerando cards (parte {n}/{len(blocos)})...")
            try:
                material = gerar_material(cliente, bloco, f"vídeo {vid}, parte {n}")
            except Exception as e:
                print(f"  ✗ Falha na IA: {e}", file=sys.stderr)
                continue

            notas = montar_notas(material, fonte)
            for nota in notas:
                deck.add_note(nota)
            simulado += material.get("simulado", [])
            print(f"  ✓ {len(notas)} cards — tema: {material.get('tema', '?')}")

    if not deck.notes:
        sys.exit("Nenhum card gerado. Verifique os links e se os vídeos têm legendas.")

    nome = tag(args.deck)
    genanki.Package(deck).write_to_file(f"{nome}.apkg")
    escrever_simulado(simulado, args.deck, f"simulado_{nome}.md")
    print(f"\n✅ {len(deck.notes)} cards em {nome}.apkg  |  simulado em simulado_{nome}.md")


if __name__ == "__main__":
    main()
