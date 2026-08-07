import asyncio
import math
import os
from threading import Thread
import aiohttp
import discord
from discord.ext import commands
from flask import Flask

# --- 1. SERVIDOR WEB (FLASK PARA REPLIT/RENDER) ---
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


# --- 2. CONFIGURAÇÃO DO BOT DISCORD ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

APPLICATION_ID = os.environ.get("APPLICATION_ID")
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")


async def find_player(nickname: str, session: aiohttp.ClientSession):
  regions = {
      "na": "https://api.wotblitz.com",
      "eu": "https://api.wotblitz.eu",
      "asia": "https://api.wotblitz.asia",
  }
  timeout = aiohttp.ClientTimeout(total=10)

  for reg_code, base_url in regions.items():
    search_url = f"{base_url}/wotb/account/list/?application_id={APPLICATION_ID}&search={nickname}"
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
                reg_code,
                base_url,
                data["data"][0]["account_id"],
                data["data"][0]["nickname"],
            )
        await asyncio.sleep(0.3)
    except Exception as e:
      print(f"Erro na busca ({reg_code}): {e}")

  return None, None, None, None


def extract_period_data(bs_json, target_period):
  """Extrai os dados do período correto navegando pela estrutura de dados do BlitzStars."""
  if not bs_json:
    return {}

  # Caso venha como lista de snapshots
  target_obj = bs_json
  if isinstance(bs_json, list) and len(bs_json) > 0:
    target_obj = bs_json[0]

  if not isinstance(target_obj, dict):
    return {}

  # Mapeamento de possíveis chaves
  keys_to_check = []
  if target_period == "24h":
    keys_to_check = ["period24h", "period1d", "period1", "24h"]
  elif target_period == "7d":
    keys_to_check = ["period7d", "period7", "7d"]
  elif target_period == "30d":
    keys_to_check = ["period30d", "period30", "30d"]
  elif target_period == "90d":
    keys_to_check = ["period90d", "period90", "90d"]

  # 1. Busca direta no objeto principal
  for k in keys_to_check:
    if k in target_obj and isinstance(target_obj[k], dict):
      return target_obj[k]

  # 2. Busca no contêiner 'periods' caso exista
  if "periods" in target_obj and isinstance(target_obj["periods"], dict):
    periods_dict = target_obj["periods"]
    for k in keys_to_check:
      if k in periods_dict and isinstance(periods_dict[k], dict):
        return periods_dict[k]

  return {}


@bot.event
async def on_ready():
  print(f"Bot conectado com sucesso como {bot.user}")


@bot.command()
async def blitz(ctx):
  """Menu interativo do bot."""

  def check(m):
    return m.author == ctx.author and m.channel == ctx.channel

  try:
    # --- PASSO 1: Escolher Função Primeiro ---
    await ctx.send(
        "🎮 **Menu Blitz**\n"
        "O que você deseja fazer?\n"
        "1️⃣ Ver Estatísticas de um Jogador\n"
        "2️⃣ Calcular Meta de Winrate\n\n"
        "*Digite `1` ou `2`:*"
    )
    msg_opcao = await bot.wait_for("message", check=check, timeout=90.0)
    opcao = msg_opcao.content.strip()

    # --- FLUXO 1: Estatísticas do Jogador ---
    if opcao == "1":
      await ctx.send(
          "Qual é o **nickname** do jogador no World of Tanks Blitz?"
      )
      msg_nick = await bot.wait_for("message", check=check, timeout=90.0)
      nickname = msg_nick.content.strip()

      await ctx.send(
          "Escolha o **período** das estatísticas:\n"
          "1️⃣ 24 Horas (`1d`)\n"
          "2️⃣ 7 Dias (`7d`)\n"
          "3️⃣ 30 Dias (`30d`)\n"
          "4️⃣ 90 Dias (`90d`)\n\n"
          "*Digite `1`, `2`, `3` ou `4`:*"
      )
      msg_periodo = await bot.wait_for("message", check=check, timeout=90.0)
      escolha = msg_periodo.content.strip().lower()

      period_map = {
          "1": ("24h", "24 Horas"),
          "1d": ("24h", "24 Horas"),
          "2": ("7d", "7 Dias"),
          "7d": ("7d", "7 Dias"),
          "3": ("30d", "30 Dias"),
          "30d": ("30d", "30 Dias"),
          "4": ("90d", "90 Dias"),
          "90d": ("90d", "90 Dias"),
      }

      period_code, period_label = period_map.get(escolha, ("24h", "24 Horas"))

      loading_msg = await ctx.send(
          f"🔍 Buscando dados de **{nickname}** nos servidores..."
      )

      headers = {
          "User-Agent": (
              "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
              " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
          ),
          "Accept": "application/json",
      }

      async with aiohttp.ClientSession(headers=headers) as session:
        region_code, base_url, account_id, player_name = await find_player(
            nickname, session
        )

        if not account_id:
          await loading_msg.edit(
              content=(
                  f"❌ Jogador **{nickname}** não foi encontrado nos"
                  " servidores NA, EU ou ASIA."
              )
          )
          return

        timeout = aiohttp.ClientTimeout(total=12)

        # 1. Força a atualização no BlitzStars (Tenta GET e POST)
        update_url = f"https://www.blitzstars.com/api/playerstats/{account_id}/update"
        try:
          async with session.get(update_url, timeout=timeout) as _:
            pass
          async with session.post(update_url, timeout=timeout) as _:
            pass
        except Exception as e:
          print(f"Aviso update BlitzStars: {e}")

        # 2. Busca os dados no BlitzStars
        blitzstars_url = (
            f"https://www.blitzstars.com/api/playerstats/{account_id}"
        )
        bs_json = None
        try:
          async with session.get(blitzstars_url, timeout=timeout) as resp:
            if resp.status == 200:
              bs_json = await resp.json()
        except Exception as e:
          print(f"Erro BlitzStars: {e}")

        # 3. Busca dados da Wargaming API (Carreira)
        info_url = f"{base_url}/wotb/account/info/?application_id={APPLICATION_ID}&account_id={account_id}"
        async with session.get(info_url, timeout=timeout) as resp:
          wg_data = await resp.json()

          if wg_data.get("status") != "ok":
            await loading_msg.edit(
                content="❌ Erro ao consultar dados na API da Wargaming."
            )
            return

          player_info = wg_data.get("data", {}).get(str(account_id))
          if not player_info or not player_info.get("statistics"):
            await loading_msg.edit(
                content=(
                    f"🔒 As estatísticas de **{player_name}** estão ocultas ou"
                    " indisponíveis."
                )
            )
            return

          stats_all = player_info["statistics"]["all"]

        # Extrai os dados do período
        period_data = extract_period_data(bs_json, period_code)
        battles_p = period_data.get("battles", 0)

        if battles_p > 0:
          wins_p = period_data.get("wins", 0)
          wr_p = (wins_p / battles_p) * 100
          avg_dmg_p = period_data.get("damage_dealt", 0) / battles_p
          periodo_txt = (
              f"• **Batalhas:** {battles_p:,}\n"
              f"• **Vitórias:** {wins_p:,}V / {battles_p - wins_p:,}D\n"
              f"• **WR:** {wr_p:.2f}%\n"
              f"• **Dano Médio:** {avg_dmg_p:.0f}"
          )
        else:
          periodo_txt = (
              f"Nenhuma partida registrada no período de {period_label}."
          )

        # Estatísticas de carreira
        battles_all = stats_all.get("battles", 0)
        wins_all = stats_all.get("wins", 0)
        wr_all = (wins_all / battles_all * 100) if battles_all > 0 else 0
        avg_dmg_all = (
            (stats_all.get("damage_dealt", 0) / battles_all)
            if battles_all > 0
            else 0
        )

        geral_txt = (
            f"• **Total de Batalhas:** {battles_all:,}\n"
            f"• **WR Geral:** {wr_all:.2f}%\n"
            f"• **Dano Médio Geral:** {avg_dmg_all:.0f}"
        )

        embed = discord.Embed(
            title=f"Estatísticas de {player_name} [{region_code.upper()}]",
            color=0x3498DB,
        )
        embed.add_field(
            name=f"📊 Desempenho ({period_label})",
            value=periodo_txt,
            inline=False,
        )
        embed.add_field(
            name="🏆 Carreira (Geral)", value=geral_txt, inline=False
        )
        embed.set_footer(text="Integrado com Wargaming API & BlitzStars")

        await loading_msg.edit(content="", embed=embed)

    # --- FLUXO 2: Calculadora de Winrate ---
    elif opcao == "2":
      await ctx.send("🧮 **Calculadora de Meta de Winrate**")

      await ctx.send("1️⃣ Quantas **batalhas no total** você tem atualmente?")
      msg1 = await bot.wait_for("message", check=check, timeout=90.0)
      try:
        partidas_atuais = int(msg1.content.strip().replace(",", ""))
      except ValueError:
        await ctx.send(
            "❌ Digite apenas números inteiros para as partidas. Exemplo:"
            " `1000`"
        )
        return

      await ctx.send(
          "2️⃣ Qual é a sua **Taxa de Vitórias (Winrate) atual** em %?\n*Exemplo:"
          " `47.5`*"
      )
      msg2 = await bot.wait_for("message", check=check, timeout=90.0)
      try:
        winrate_atual = float(
            msg2.content.strip().replace("%", "").replace(",", ".")
        )
      except ValueError:
        await ctx.send(
            "❌ Digite um número válido para o Winrate atual. Exemplo: `47.5`"
        )
        return

      await ctx.send(
          "3️⃣ Qual **Winrate recente** você pretende manter/jogar a partir de"
          " agora em %?\n*Exemplo: `53`*"
      )
      msg3 = await bot.wait_for("message", check=check, timeout=90.0)
      try:
        winrate_recente = float(
            msg3.content.strip().replace("%", "").replace(",", ".")
        )
      except ValueError:
        await ctx.send(
            "❌ Digite um número válido para o Winrate recente. Exemplo: `53`"
        )
        return

      await ctx.send(
          "4️⃣ Qual é a sua **Meta de Winrate (Alvo)** em %?\n*Exemplo: `50`*"
      )
      msg4 = await bot.wait_for("message", check=check, timeout=90.0)
      try:
        winrate_alvo = float(
            msg4.content.strip().replace("%", "").replace(",", ".")
        )
      except ValueError:
        await ctx.send(
            "❌ Digite um número válido para a meta de Winrate. Exemplo: `50`"
        )
        return

      p_atual = winrate_atual / 100
      p_recente = winrate_recente / 100
      p_alvo = winrate_alvo / 100

      if p_recente <= p_alvo:
        await ctx.send(
            "❌ **Sua taxa recente precisa ser MAIOR que a taxa alvo** para"
            " você conseguir subir seu WR geral."
        )
        return

      if p_alvo <= p_atual:
        await ctx.send(
            "❌ **A taxa alvo precisa ser MAIOR que a sua taxa atual.**"
        )
        return

      partidas_restantes = math.ceil(
          partidas_atuais * (p_alvo - p_atual) / (p_recente - p_alvo)
      )
      total_final = partidas_atuais + partidas_restantes

      embed = discord.Embed(title="🧮 Meta de Winrate", color=0x2ECC71)
      embed.add_field(
          name="📌 Dados Informados",
          value=(
              f"• Partidas Atuais: **{partidas_atuais:,}**\n"
              f"• WR Atual: **{winrate_atual:.2f}%**\n"
              f"• WR Recente Assumido: **{winrate_recente:.2f}%**\n"
              f"• WR Alvo: **{winrate_alvo:.2f}%**"
          ),
          inline=False,
      )
      embed.add_field(
          name="🎯 Resultado",
          value=(
              f"• Partidas Faltantes: **{partidas_restantes:,}**\n"
              f"• Total de Partidas ao Final: **{total_final:,}**"
          ),
          inline=False,
      )
      embed.set_footer(
          text=f"Mantendo {winrate_recente:.1f}% de taxa recente constantes."
      )

      await ctx.send(embed=embed)

    else:
      await ctx.send(
          "❌ Opção inválida! Execute `!blitz` novamente e escolha `1` ou `2`."
      )

  except asyncio.TimeoutError:
    await ctx.send(
        "⏰ **Tempo esgotado!** Você demorou muito para responder. Digite"
        " `!blitz` para tentar novamente."
    )
  except Exception as e:
    await ctx.send(f"❌ Ocorreu um erro ao processar: `{e}`")


# --- 3. INICIALIZAÇÃO ---
keep_alive()
bot.run(DISCORD_TOKEN)
