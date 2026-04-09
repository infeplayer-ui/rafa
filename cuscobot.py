import discord
from discord.ext import tasks
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import os

load_dotenv()

# ──────────────────────────────────────────
#  CONFIGURAÇÃO  ←  edita aqui
# ──────────────────────────────────────────
BOT_TOKEN      = os.getenv("BOT_TOKEN")
CANAL_ID       = 1491902094603452589
RESUMO_HORA    = 22
RESUMO_MINUTO  = 0
MONITORIZAR    = {448949606257131530}
# ──────────────────────────────────────────

intents = discord.Intents.default()
intents.presences       = True
intents.members         = True
intents.message_content = True

bot = discord.Client(intents=intents)

sessoes_ativas: dict[int, dict] = {}
historico_hoje: dict[int, dict[str, timedelta]] = {}


def adicionar_tempo(user_id: int, jogo: str, duracao: timedelta):
    if user_id not in historico_hoje:
        historico_hoje[user_id] = {}
    historico_hoje[user_id][jogo] = historico_hoje[user_id].get(jogo, timedelta()) + duracao


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


@bot.event
async def on_ready():
    print(f"✅  Bot ligado como {bot.user}")

    for guild in bot.guilds:
        for uid in MONITORIZAR:
            membro = guild.get_member(uid)
            if membro:
                for activity in membro.activities:
                    if isinstance(activity, (discord.Game, discord.Activity)):
                        jogo   = activity.name
                        inicio = to_naive_utc(activity.start) if activity.start else datetime.now(timezone.utc).replace(tzinfo=None)
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

    # ── Terminou de jogar ──────────────────────────────────────────────────────
    if jogo_ant and uid in sessoes_ativas:
        sessao  = sessoes_ativas.pop(uid)
        duracao = agora - sessao["inicio"]
        adicionar_tempo(uid, sessao["jogo"], duracao)
        if canal:
            await canal.send(
                f"🔴 **{nome}** parou de jogar **{sessao['jogo']}** "
                f"após **{formatar_duracao(duracao)}**!"
            )

    # ── Começou a jogar ────────────────────────────────────────────────────────
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
    if message.author.bot or message.content.lower() != "!check":
        return

    agora = datetime.now(timezone.utc).replace(tzinfo=None)
    canal = message.channel

    for uid in MONITORIZAR:
        membro = message.guild.get_member(uid) if message.guild else None
        nome   = membro.display_name if membro else f"<@{uid}>"

        if uid not in sessoes_ativas:
            await canal.send(f"**{nome}** não está a jogar nada agora.")
            return

        sessao     = sessoes_ativas[uid]
        jogo       = sessao["jogo"]
        parcial    = agora - sessao["inicio"]
        total_hoje = historico_hoje.get(uid, {}).get(jogo, timedelta()) + parcial

        await canal.send(
            f"🎮 **{nome}** está a jogar **{jogo}** há **{formatar_duracao(parcial)}** "
            f"(total hoje: **{formatar_duracao(total_hoje)}**)"
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
    agora = datetime.now(timezone.utc).replace(tzinfo=None)
    if agora.hour != RESUMO_HORA or agora.minute != RESUMO_MINUTO:
        return

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
