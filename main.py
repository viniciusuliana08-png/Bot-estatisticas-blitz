import asyncio
import os
from threading import Thread
import aiohttp
import discord
from discord.ext import commands
from flask import Flask

# --- 1. CONFIGURAÇÃO DO SERVIDOR WEB (FLASK) ---
app = Flask("")


@app.route("/")
def home():
  return "Bot online!"


def run():
  port = int(os.environ.get("PORT", 10000))
  app.run(host="0.0.0.0", port=port)


def keep_alive():
  t = Thread(target=run)
  t.start()


# --- 2. CONFIGURAÇÃO DO BOT DO DISCORD ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

APPLICATION_ID = os.environ.get("APPLICATION_ID")
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")


async def find_player(nickname: str, session: aiohttp.ClientSession):
  regions = ["na", "eu", "asia"]
  timeout = aiohttp.ClientTimeout(total=10)

  for region in regions:
    search_url = f"https://api.wotblitz.{region}/wotb/account/list/?application_id={APPLICATION_ID}&search={nickname}"
    try:
      async with session.get(search_url, timeout=timeout) as resp:
        if resp.status == 200:
          data = await resp.json()
          if (
              data.get("status") == "ok"
              and data.get("data")
              and len(data["data"]) > 0
          ):
            return (
                region,
                data["data"][0]["account_id"],
                data["data"][0]["nickname"],
            )
        await asyncio.sleep(0.3)
    except Exception as e:
      print(f"Erro na busca da região {region}: {e}")

  return None, None, None


@bot.event
async def on_ready():
  print(f"Bot online com sucesso como {bot.user}")


@bot.command()
async def blitz(ctx, *, nickname: str):
  """Sintaxe: !blitz SeuNickAqui"""
  msg = await ctx.send(f"Procurando por **{nickname}** nos servidores...")

  try:
    async with aiohttp.ClientSession() as session:
      # Busca o jogador nas regiões NA, EU e ASIA
      region, account_id, player_name = await find_player(nickname, session)

      if not account_id:
        await msg.edit(
            content=(
                f"Jogador **{nickname}** não foi encontrado ou a API não"
                " respondeu."
            )
        )
        return

      timeout = aiohttp.ClientTimeout(total=10)

      # 1. Dados Diários (BlitzStars)
      blitzstars_url = f"https://www.blitzstars.com/api/playerstats/{account_id}"
      bs_data = {}
      try:
        async with session.get(blitzstars_url, timeout=timeout) as resp:
          if resp.status == 200:
            bs_data = await resp.json()
      except Exception as e:
        print(f"Erro no BlitzStars: {e}")

      # 2. Dados Gerais (Wargaming API)
      info_url = f"https://api.wotblitz.{region}/wotb/account/info/?application_id={APPLICATION_ID}&account_id={account_id}"
      async with session.get(info_url, timeout=timeout) as resp:
        wg_data = await resp.json()
        if wg_data.get("status") != "ok":
          await msg.edit(
              content="Erro ao consultar dados gerais da API Wargaming."
          )
          return
        stats_all = wg_data["data"][str(account_id)]["statistics"]["all"]

      # Formatação dos dados de 24h
      p24 = bs_data.get("period24h", {}) if isinstance(bs_data, dict) else {}
      battles_24 = p24.get("battles", 0)

      if battles_24 > 0:
        wins_24 = p24.get("wins", 0)
        wr_24 = (wins_24 / battles_24) * 100
        avg_dmg_24 = p24.get("damage_dealt", 0) / battles_24
        diario_txt = (
            f"**Batalhas:** {battles_24}\n"
            f"**Vitórias:** {wins_24}V / {battles_24 - wins_24}D\n"
            f"**WR Diário:** {wr_24:.2f}%\n"
            f"**Dano Médio:** {avg_dmg_24:.0f}"
        )
      else:
        diario_txt = "Nenhuma partida registrada nas últimas 24h."

      # Formatação dos dados Gerais
      battles_all = stats_all.get("battles", 0)
      wins_all = stats_all.get("wins", 0)
      wr_all = (wins_all / battles_all * 100) if battles_all > 0 else 0
      avg_dmg_all = (
          (stats_all.get("damage_dealt", 0) / battles_all)
          if battles_all > 0
          else 0
      )

      geral_txt = (
          f"**Total de Batalhas:** {battles_all:,}\n"
          f"**WR Geral:** {wr_all:.2f}%\n"
          f"**Dano Médio Geral:** {avg_dmg_all:.0f}"
      )

      # Montagem do Embed do Discord
      embed = discord.Embed(
          title=f"Estatísticas de {player_name} [{region.upper()}]",
          color=discord.Color.gold(),
      )
      embed.add_field(
          name="Desempenho Hoje (24h)", value=diario_txt, inline=False
      )
      embed.add_field(name="Carreira (Geral)", value=geral_txt, inline=False)
      embed.set_footer(text="Integrado com Wargaming API & BlitzStars")

      await msg.edit(content=None, embed=embed)

  except Exception as e:
    print(f"ERRO NO COMANDO BLITZ: {e}")
    await msg.edit(content=f"Ocorreu um erro ao processar o comando: `{e}`")


# --- 3. INICIALIZAÇÃO ---
keep_alive()
bot.run(DISCORD_TOKEN)
