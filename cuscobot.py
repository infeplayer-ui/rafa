import discord
from discord.ext import tasks
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
import os
import json
import random
import aiohttp

load_dotenv()

# ──────────────────────────────────────────
#  CONFIGURAÇÃO
# ──────────────────────────────────────────
BOT_TOKEN        = os.getenv("BOT_TOKEN")
FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY")
ODDS_API_KEY     = os.getenv("ODDS_API_KEY")
CANAL_ID         = 1491902094603452589
CANAL_FUTEBOL_ID = 1492934881976516914
RESUMO_HORA      = 0
RESUMO_MINUTO    = 0
TIMEZONE         = ZoneInfo("Europe/Lisbon")
MONITORIZAR      = {448949606257131530}
LISTA_NEGRA = {
    175668435240353792: "Caladinho, tu comes gordas"
}
FICHEIRO_TOTAL = "/data/total_global.json"

# Competições football-data.org
COMPETICOES = {
    "CL":  "🏆 Champions League",
    "EL":  "🟠 Europa League",
    "ECL": "⚪ Conference League",
    "PL":  "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Premier League",
    "PPL": "🇵🇹 Primeira Liga",
    "SA":  "🇮🇹 Serie A",
    "FL1": "🇫🇷 Ligue 1",
    "PD":  "🇪🇸 La Liga",
}

# Competições The Odds API
ODDS_SPORTS = [
    "soccer_uefa_champs_league",
    "soccer_uefa_europa_league",
    "soccer_uefa_europa_conference_league",
    "soccer_epl",
    "soccer_portugal_primeira_liga",
    "soccer_italy_serie_a",
    "soccer_france_ligue_one",
    "soccer_spain_la_liga",
]
# ──────────────────────────────────────────

intents = discord.Intents.default()
intents.presences       = True
intents.members         = True
intents.message_content = True

bot = discord.Client(intents=intents)

sessoes_ativas: dict[int, dict] = {}
historico_hoje: dict[int, dict[str, timedelta]] = {}


def carregar_total() -> dict[int, timedelta]:
    if os.path.exists(FICHEIRO_TOTAL):
        with open(FICHEIRO_TOTAL, "r") as f:
            dados = json.load(f)
            return {int(uid): timedelta(seconds=s) for uid, s in dados.items()}
    return {}


def guardar_total():
    dados = {str(uid): td.total_seconds() for uid, td in total_global.items()}
    with open(FICHEIRO_TOTAL, "w") as f:
        json.dump(dados, f)


total_global: dict[int, timedelta] = carregar_total()


def adicionar_tempo(user_id: int, jogo: str, duracao: timedelta):
    if user_id not in historico_hoje:
        historico_hoje[user_id] = {}
    historico_hoje[user_id][jogo] = historico_hoje[user_id].get(jogo, timedelta()) + duracao
    total_global[user_id] = total_global.get(user_id, timedelta()) + duracao
    guardar_total()


def formatar_duracao(td: timedelta) -> str:
    total = int(td.total_seconds())
    horas, resto = divmod(total, 3600)
    minutos = resto // 60
    if horas and minutos:
        return f"{horas}h {minutos}min"
    if horas:
        return f"{horas}h"
    return f"{minutos}min"


def jogo_da_presenca(member: discord.Member) -> str | None:
    for activity in member.activities:
        if isinstance(activity, discord.Game):
            return activity.name
        if isinstance(activity, discord.Activity) and activity.type == discord.ActivityType.playing:
            return activity.name
    return None


def to_naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def formatar_hora(utc_str: str) -> str:
    try:
        dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        return dt.astimezone(TIMEZONE).strftime("%H:%M")
    except:
        return "?"


async def buscar_jogos_hoje() -> dict[str, list]:
    """Busca jogos de hoje via football-data.org."""
    hoje = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    resultado = {}
    async with aiohttp.ClientSession() as session:
        for codigo, nome in COMPETICOES.items():
            url     = f"https://api.football-data.org/v4/competitions/{codigo}/matches"
            headers = {"X-Auth-Token": FOOTBALL_API_KEY}
            params  = {"dateFrom": hoje, "dateTo": hoje}
            try:
                async with session.get(url, headers=headers, params=params) as resp:
                    if resp.status == 200:
                        data  = await resp.json()
                        jogos = data.get("matches", [])
                        if jogos:
                            resultado[nome] = jogos
            except Exception as e:
                print(f"Erro a buscar {codigo}: {e}")
    return resultado


async def buscar_odds_hoje() -> list[dict]:
    """
    Busca odds reais de hoje via The Odds API.
    Devolve lista de jogos com odds h2h (1X2).
    Cada item: { casa, fora, hora, odds: {casa: float, empate: float, fora: float} }
    """
    agora     = datetime.now(timezone.utc)
    hoje_fim  = agora.replace(hour=23, minute=59, second=59)
    resultado = []

    async with aiohttp.ClientSession() as session:
        for sport in ODDS_SPORTS:
            url    = f"https://api.the-odds-api.com/v4/sports/{sport}/odds/"
            params = {
                "apiKey":  ODDS_API_KEY,
                "regions": "eu",
                "markets": "h2h",
                "oddsFormat": "decimal",
            }
            try:
                async with session.get(url, params=params) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()
                    for jogo in data:
                        # Filtra apenas jogos de hoje
                        try:
                            commence = datetime.fromisoformat(
                                jogo["commence_time"].replace("Z", "+00:00")
                            ).astimezone(timezone.utc).replace(tzinfo=None)
                        except:
                            continue

                        agora_naive   = agora.replace(tzinfo=None)
                        hoje_fim_naive = hoje_fim.replace(tzinfo=None)

                        if not (agora_naive <= commence <= hoje_fim_naive):
                            continue

                        # Extrai odds da primeira bookmaker disponível
                        bookmakers = jogo.get("bookmakers", [])
                        if not bookmakers:
                            continue

                        odds_raw = {}
                        for bm in bookmakers:
                            for market in bm.get("markets", []):
                                if market["key"] == "h2h":
                                    for outcome in market["outcomes"]:
                                        odds_raw[outcome["name"]] = outcome["price"]
                                    break
                            if odds_raw:
                                break

                        if len(odds_raw) < 2:
                            continue

                        home_team = jogo["home_team"]
                        away_team = jogo["away_team"]
                        hora      = formatar_hora(jogo["commence_time"])

                        odd_casa   = odds_raw.get(home_team)
                        odd_fora   = odds_raw.get(away_team)
                        odd_empate = None
                        for k, v in odds_raw.items():
                            if k not in (home_team, away_team):
                                odd_empate = v
                                break

                        resultado.append({
                            "casa":  home_team,
                            "fora":  away_team,
                            "hora":  hora,
                            "odds": {
                                "casa":   odd_casa,
                                "empate": odd_empate,
                                "fora":   odd_fora,
                            }
                        })
            except Exception as e:
                print(f"Erro odds {sport}: {e}")

    return resultado


def selecoes_possiveis(jogo: dict, max_odd: float | None) -> list[tuple[str, float]]:
    """Devolve lista de (descrição, odd) possíveis para um jogo, filtradas por max_odd."""
    odds  = jogo["odds"]
    casa  = jogo["casa"]
    fora  = jogo["fora"]
    opcoes = []
    if odds.get("casa"):
        opcoes.append((f"Vitória **{casa}**", odds["casa"]))
    if odds.get("empate"):
        opcoes.append(("Empate", odds["empate"]))
    if odds.get("fora"):
        opcoes.append((f"Vitória **{fora}**", odds["fora"]))

    if max_odd is not None:
        opcoes = [(d, o) for d, o in opcoes if o <= max_odd]
    return opcoes


def gerar_aposta_single(jogos: list[dict], max_odd: float | None) -> str:
    candidatos = []
    for jogo in jogos:
        for descricao, odd in selecoes_possiveis(jogo, max_odd):
            candidatos.append((jogo, descricao, odd))

    if not candidatos:
        limite = f" com odd ≤ {max_odd}" if max_odd else ""
        return f"❌ Não há seleções disponíveis{limite}."

    jogo, descricao, odd = random.choice(candidatos)
    return (
        f"🎯 **Aposta Single**\n"
        f"⚽ **{jogo['casa']}** vs **{jogo['fora']}** ({jogo['hora']})\n"
        f"📌 {descricao}\n"
        f"💰 Odd: **{odd}**"
    )


def gerar_aposta_multipla(jogos: list[dict], n: int, max_odd_total: float | None) -> str:
    if not jogos:
        return "❌ Não há jogos disponíveis para gerar uma múltipla."

    # Máximo tentativas para respeitar a odd total
    for _ in range(200):
        if len(jogos) < n:
            selecionados = jogos[:]
        else:
            selecionados = random.sample(jogos, n)

        linhas    = []
        odd_total = 1.0
        valido    = True

        for jogo in selecionados:
            opcoes = selecoes_possiveis(jogo, None)
            if not opcoes:
                valido = False
                break
            descricao, odd = random.choice(opcoes)
            odd_total *= odd
            linhas.append(
                f"⚽ **{jogo['casa']}** vs **{jogo['fora']}** ({jogo['hora']}) "
                f"→ {descricao} @ **{odd}**"
            )

        if not valido:
            continue
        if max_odd_total is not None and odd_total > max_odd_total:
            continue

        num_real = len(selecionados)
        aviso = ""
        if num_real < n:
            aviso = f"\n⚠️ Só havia **{num_real}** jogos disponíveis hoje."

        return (
            f"🎯 **Aposta Múltipla ({num_real} jogos)**\n\n"
            + "\n".join(linhas)
            + f"\n\n💰 Odd total: **{round(odd_total, 2)}**"
            + aviso
        )

    limite = f" com odd total ≤ {max_odd_total}" if max_odd_total else ""
    return f"❌ Não foi possível gerar uma múltipla{limite}. Tenta com uma odd mais alta."


@bot.event
async def on_ready():
    print(f"✅  Bot ligado como {bot.user}")
    print(f"📂  Total global carregado: {total_global}")

    agora_local   = datetime.now(TIMEZONE).replace(hour=0, minute=0, second=0, microsecond=0)
    inicio_do_dia = agora_local.astimezone(timezone.utc).replace(tzinfo=None)

    for guild in bot.guilds:
        for uid in MONITORIZAR:
            membro = guild.get_member(uid)
            if membro:
                for activity in membro.activities:
                    if isinstance(activity, (discord.Game, discord.Activity)):
                        jogo   = activity.name
                        inicio = to_naive_utc(activity.start) if activity.start else datetime.now(timezone.utc).replace(tzinfo=None)
                        inicio = max(inicio, inicio_do_dia)
                        sessoes_ativas[uid] = {"jogo": jogo, "inicio": inicio}
                        print(f"▶️  {membro.display_name} já está a jogar {jogo} desde {inicio}")
                        break

    resumo_diario.start()
    notificacao_hora.start()


@bot.event
async def on_presence_update(before: discord.Member, after: discord.Member):
    uid       = after.id
    nome      = after.display_name
    jogo_ant  = jogo_da_presenca(before)
    jogo_novo = jogo_da_presenca(after)

    if uid not in MONITORIZAR:
        return
    if jogo_ant == jogo_novo:
        return

    canal = bot.get_channel(CANAL_ID)
    agora = datetime.now(timezone.utc).replace(tzinfo=None)

    if jogo_ant and uid in sessoes_ativas:
        sessao  = sessoes_ativas.pop(uid)
        duracao = agora - sessao["inicio"]
        adicionar_tempo(uid, sessao["jogo"], duracao)
        if canal:
            await canal.send(
                f"🔴 **{nome}** parou de jogar **{sessao['jogo']}** "
                f"após **{formatar_duracao(duracao)}**!"
            )

    if jogo_novo:
        inicio = datetime.now(timezone.utc).replace(tzinfo=None)
        for activity in after.activities:
            if isinstance(activity, (discord.Game, discord.Activity)) and activity.name == jogo_novo:
                if activity.start:
                    inicio = to_naive_utc(activity.start)
                break
        sessoes_ativas[uid] = {"jogo": jogo_novo, "inicio": inicio}
        if canal:
            await canal.send(f"🟢 **{nome}** começou a jogar **{jogo_novo}**!")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    if message.author.id in LISTA_NEGRA:
        await message.channel.send(LISTA_NEGRA[message.author.id])
        return

    # ── !check ────────────────────────────────────────────────────────────────
    if message.content.lower() == "!check":
        agora = datetime.now(timezone.utc).replace(tzinfo=None)
        canal = message.channel

        for uid in MONITORIZAR:
            membro = message.guild.get_member(uid) if message.guild else None
            nome   = membro.display_name if membro else f"<@{uid}>"

            if uid not in sessoes_ativas:
                await canal.send(f"**{nome}** não está a jogar nada agora.")
                return

            sessao      = sessoes_ativas[uid]
            jogo        = sessao["jogo"]
            parcial     = agora - sessao["inicio"]
            total_hoje  = historico_hoje.get(uid, {}).get(jogo, timedelta()) + parcial
            total_geral = total_global.get(uid, timedelta()) + parcial
            horas_geral = total_geral.total_seconds() / 3600

            livros   = horas_geral / 6
            filmes   = horas_geral / 2
            km       = horas_geral * 5
            trabalho = horas_geral / 8

            comparacoes = []
            if livros >= 0.1:
                comparacoes.append(f"📚 ler **{livros:.1f} livros**")
            if filmes >= 0.1:
                comparacoes.append(f"🎬 ver **{filmes:.1f} filmes**")
            if km >= 0.5:
                comparacoes.append(f"🚶 andar **{km:.1f} km** a pé")
            if trabalho >= 0.1:
                comparacoes.append(f"💼 **{trabalho:.1f} dias** de trabalho full-time")

            comp_str = "\nCom o tempo total dava para:\n" + "\n".join(f"  • {c}" for c in comparacoes) if comparacoes else ""

            await canal.send(
                f"🎮 **{nome}** está a jogar **{jogo}** há **{formatar_duracao(parcial)}**\n"
                f"📅 Total hoje: **{formatar_duracao(total_hoje)}**\n"
                f"🏆 Total de sempre: **{formatar_duracao(total_geral)}**\n"
                f"{comp_str}"
            )

    # ── !jogos ────────────────────────────────────────────────────────────────
    elif message.content.lower() == "!jogos":
        if message.channel.id != CANAL_FUTEBOL_ID:
            await message.channel.send(f"⚽ Este comando só funciona em <#{CANAL_FUTEBOL_ID}>!")
            return

        await message.channel.send("⏳ A buscar jogos de hoje...")
        jogos_por_comp = await buscar_jogos_hoje()

        if not jogos_por_comp:
            await message.channel.send("Não há jogos hoje nas competições monitorizadas.")
            return

        for comp, jogos in jogos_por_comp.items():
            linhas = [f"**{comp}**"]
            for j in jogos:
                casa   = j["homeTeam"]["shortName"]
                fora   = j["awayTeam"]["shortName"]
                hora   = formatar_hora(j["utcDate"])
                estado = j.get("status", "")
                if estado == "FINISHED":
                    g_casa = j["score"]["fullTime"]["home"]
                    g_fora = j["score"]["fullTime"]["away"]
                    linhas.append(f"  ✅ {casa} **{g_casa} - {g_fora}** {fora}")
                elif estado in ("IN_PLAY", "PAUSED"):
                    g_casa = j["score"]["fullTime"]["home"] or 0
                    g_fora = j["score"]["fullTime"]["away"] or 0
                    linhas.append(f"  🔴 {casa} **{g_casa} - {g_fora}** {fora} *(a decorrer)*")
                else:
                    linhas.append(f"  🕐 {hora} — {casa} vs {fora}")
            await message.channel.send("\n".join(linhas))

    # ── !aposta ───────────────────────────────────────────────────────────────
    elif message.content.lower().startswith("!aposta"):
        if message.channel.id != CANAL_FUTEBOL_ID:
            await message.channel.send(f"⚽ Este comando só funciona em <#{CANAL_FUTEBOL_ID}>!")
            return

        partes = message.content.split()
        # !aposta single [max_odd]
        # !aposta multipla [n] [max_odd_total]

        await message.channel.send("⏳ A ir buscar odds reais...")
        jogos = await buscar_odds_hoje()

        if not jogos:
            await message.channel.send("❌ Não foi possível obter odds para hoje. Tenta mais tarde.")
            return

        tipo = partes[1].lower() if len(partes) > 1 else "multipla"

        if tipo == "single":
            max_odd = None
            if len(partes) > 2:
                try:
                    max_odd = float(partes[2])
                except ValueError:
                    pass
            await message.channel.send(gerar_aposta_single(jogos, max_odd))

        else:  # multipla
            n = 3
            max_odd_total = None
            if len(partes) > 2:
                try:
                    n = int(partes[2])
                except ValueError:
                    pass
            if len(partes) > 3:
                try:
                    max_odd_total = float(partes[3])
                except ValueError:
                    pass
            await message.channel.send(gerar_aposta_multipla(jogos, n, max_odd_total))

    # ── !clear ────────────────────────────────────────────────────────────────
    elif message.content.lower() == "!clear":
        await message.channel.purge()

    # ── !chatear ──────────────────────────────────────────────────────────────
    elif message.content.lower() == "!chatear":
        user = await bot.fetch_user(1121848584967569408)
        await user.send("Mini manny, para de gritar")
        await message.channel.send("✅ Mensagem enviada!")

    # ── !alert ────────────────────────────────────────────────────────────────
    elif message.content.lower() == "!alert":
        user = await bot.fetch_user(570368146310037555)
        await user.send("Já chega de jogar maroto")
        await message.channel.send("✅ Alerta enviado!")

    # ── !settotal ─────────────────────────────────────────────────────────────
    elif message.content.lower().startswith("!settotal"):
        partes = message.content.split()
        if len(partes) != 2:
            await message.channel.send("Uso: `!settotal <horas>`")
            return
        try:
            horas = float(partes[1])
            for uid in MONITORIZAR:
                total_global[uid] = timedelta(hours=horas)
            guardar_total()
            await message.channel.send(f"✅ Total atualizado para **{horas}h**!")
        except ValueError:
            await message.channel.send("❌ Valor inválido. Usa um número, ex: `!settotal 42.5`")

    # ── !help ─────────────────────────────────────────────────────────────────
    elif message.content.lower() == "!help":
        if message.channel.id == CANAL_FUTEBOL_ID:
            await message.channel.send(
                "**📋 Comandos disponíveis:**\n\n"
                "⚽ `!jogos` — mostra todos os jogos de hoje\n"
                "🎲 `!aposta single [odd_max]` — aposta simples com odds reais\n"
                "   ex: `!aposta single 2.5` — só seleções com odd ≤ 2.5\n"
                "🎲 `!aposta multipla [n] [odd_total_max]` — aposta múltipla com odds reais\n"
                "   ex: `!aposta multipla 4 10.0` — 4 jogos com odd total ≤ 10\n"
                "🗑️ `!clear` — limpa todas as mensagens do canal\n"
                "❓ `!help` — mostra esta mensagem\n"
            )
        else:
            await message.channel.send(
                "**📋 Comandos disponíveis:**\n\n"
                "🎮 `!check` — vê as horas que já jogou na sessão atual, o total do dia e as comparações do tempo perdido 😄\n"
                "🗑️ `!clear` — limpa todas as mensagens do canal\n"
                "⚙️ `!settotal <horas>` — define manualmente o total de horas de sempre (ex: `!settotal 1048`)\n"
                "🚨 `!alert` — manda uma DM ao Paiva a dizer que já chega de jogar\n"
                "📢 `!chatear` — manda uma DM ao Mini manny a dizer para parar de gritar\n"
                "❓ `!help` — mostra esta mensagem\n"
            )


@tasks.loop(minutes=60)
async def notificacao_hora():
    agora = datetime.now(timezone.utc).replace(tzinfo=None)
    canal = bot.get_channel(CANAL_ID)
    if not canal:
        return

    for uid in MONITORIZAR:
        if uid not in sessoes_ativas:
            continue

        sessao     = sessoes_ativas[uid]
        jogo       = sessao["jogo"]
        parcial    = agora - sessao["inicio"]
        total_hoje = historico_hoje.get(uid, {}).get(jogo, timedelta()) + parcial

        if parcial.total_seconds() < 3600:
            continue

        membro = canal.guild.get_member(uid)
        nome   = membro.display_name if membro else f"<@{uid}>"

        await canal.send(
            f"⏰ **{nome}** passou mais uma hora a jogar **{jogo}**! "
            f"(Total hoje: **{formatar_duracao(total_hoje)}**)"
        )


@tasks.loop(minutes=1)
async def resumo_diario():
    agora_local = datetime.now(TIMEZONE)
    if agora_local.hour != RESUMO_HORA or agora_local.minute != RESUMO_MINUTO:
        return

    agora = datetime.now(timezone.utc).replace(tzinfo=None)

    for uid, sessao in list(sessoes_ativas.items()):
        duracao = agora - sessao["inicio"]
        adicionar_tempo(uid, sessao["jogo"], duracao)
        sessoes_ativas[uid]["inicio"] = agora

    canal = bot.get_channel(CANAL_ID)
    if not canal or not historico_hoje:
        historico_hoje.clear()
        return

    linhas = ["📊 **Resumo de hoje** 📊\n"]
    for uid, jogos in historico_hoje.items():
        membro = canal.guild.get_member(uid)
        nome   = membro.display_name if membro else f"<@{uid}>"
        for jogo, total in sorted(jogos.items(), key=lambda x: x[1], reverse=True):
            linhas.append(f"• **{nome}** jogou **{jogo}** por **{formatar_duracao(total)}**")

    await canal.send("\n".join(linhas))
    historico_hoje.clear()


bot.run(BOT_TOKEN)
